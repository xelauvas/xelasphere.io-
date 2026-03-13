import os
import requests
import uuid
import hashlib
import base64
import re
import time
from pathlib import Path
from flask import Blueprint, request, session, redirect, url_for, flash, render_template, current_app
from firebase_admin import auth  # Firebase is initialized in config.py
from urllib.parse import urlencode
from app.config import Config, db  # Import Config class and db
from .grog_api import TaskGenerator
from .limiter import limiter, get_auth_rate_limit_key, csrf_required
from .model_catalog import MODEL_LABELS
from datetime import timedelta
from markupsafe import Markup
import logging
from datetime import datetime
import random
from google.cloud import firestore as gc_firestore
from werkzeug.utils import secure_filename
from typing import Any, Dict, Optional


auth_routes = Blueprint('auth_routes', __name__)

# OAuth Configurations using values from Config
GOOGLE_CLIENT_ID = Config.GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET = Config.GOOGLE_CLIENT_SECRET
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_USER_INFO_URI = "https://www.googleapis.com/oauth2/v1/userinfo"

MICROSOFT_CLIENT_ID = Config.MICROSOFT_CLIENT_ID
MICROSOFT_CLIENT_SECRET = Config.MICROSOFT_CLIENT_SECRET
MICROSOFT_AUTH_URI = Config.MICROSOFT_AUTH_URI
MICROSOFT_TOKEN_URI = Config.MICROSOFT_TOKEN_URI
MICROSOFT_USER_INFO_URI = Config.MICROSOFT_USER_INFO_URI

DISCORD_CLIENT_ID = Config.DISCORD_CLIENT_ID
DISCORD_CLIENT_SECRET = Config.DISCORD_CLIENT_SECRET
DISCORD_AUTH_URI = Config.DISCORD_AUTH_URI
DISCORD_TOKEN_URI = Config.DISCORD_TOKEN_URI
DISCORD_USER_INFO_URI = Config.DISCORD_USER_INFO_URI


def get_dynamic_redirect_uri(provider: str) -> str:
    """
    Build OAuth redirect URI dynamically based on the current request's host.
    This allows the same code to work with localhost, ngrok, and production domains.
    """
    # Get the scheme - check multiple headers that proxies might use
    scheme = (
        request.headers.get('X-Forwarded-Proto') or
        request.headers.get('X-Scheme') or
        request.scheme
    )

    # Get the host - ngrok typically sets the Host header correctly
    # Also check X-Forwarded-Host and X-Original-Host
    host = (
        request.headers.get('X-Forwarded-Host') or
        request.headers.get('X-Original-Host') or
        request.host
    )

    # Remove port if it's the default for the scheme
    if ':' in host:
        host_parts = host.split(':')
        if (scheme == 'https' and host_parts[1] == '443') or (scheme == 'http' and host_parts[1] == '80'):
            host = host_parts[0]

    # Build the base URL
    base_url = f"{scheme}://{host}"

    # Map provider to callback path
    callback_paths = {
        'google': '/auth/google/callback',
        'microsoft': '/auth/microsoft/callback',
        'discord': '/auth/discord/callback'
    }

    callback_path = callback_paths.get(provider, f'/auth/{provider}/callback')
    redirect_uri = f"{base_url}{callback_path}"

    # Log all relevant headers for debugging
    logger.info(f"OAuth redirect URI debug - Host header: {request.host}, "
                f"X-Forwarded-Host: {request.headers.get('X-Forwarded-Host')}, "
                f"X-Forwarded-Proto: {request.headers.get('X-Forwarded-Proto')}, "
                f"Final URI: {redirect_uri}")

    return redirect_uri

# Configuration status check
GOOGLE_OAUTH_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
MICROSOFT_OAUTH_CONFIGURED = bool(MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET)
DISCORD_OAUTH_CONFIGURED = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)

# Log OAuth configuration status 
logger = logging.getLogger("auth")
logger.info(f"OAuth Configuration Status - Google: {GOOGLE_OAUTH_CONFIGURED}")
logger.info(f"OAuth Configuration Status - Microsoft: {MICROSOFT_OAUTH_CONFIGURED}")
logger.info(f"OAuth Configuration Status - Discord: {DISCORD_OAUTH_CONFIGURED}")

ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


# Utility functions for PKCE (Proof Key for Code Exchange)
def generate_code_verifier():
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('utf-8')

def generate_code_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')

def get_password_reset_link(email):
    """Generate password reset link with improved error handling."""
    try:
        # Generate password reset link
        action_code_settings = auth.ActionCodeSettings(
            url=url_for('auth_routes.handle_reset', _external=True),
            handle_code_in_app=False
        )
        
        reset_link = auth.generate_password_reset_link(
            email,
            action_code_settings=action_code_settings
        )
        return True, reset_link
        
    except auth.UserNotFoundError:
        # Don't reveal user existence
        return True, None
        
    except FirebaseError as e:
        current_app.logger.error(f"Firebase error in reset link generation: {str(e)}")
        if "INVALID_EMAIL" in str(e):
            return False, "Invalid email address format."
        elif "RESET_PASSWORD_EXCEED_LIMIT" in str(e):
            return False, "Too many reset attempts. Please try again later."
        else:
            return False, "Failed to generate reset link."
        
    except ValueError as e:
        current_app.logger.error(f"Value error in reset link generation: {str(e)}")
        return False, "Invalid email address format."
        
    except Exception as e:
        current_app.logger.error(f"Unexpected error in reset link generation: {str(e)}")
        return False, "An unexpected error occurred."


@auth_routes.route('/auth/reset-password', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=get_auth_rate_limit_key)
def reset_password():
    """Reset password request."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if not email:
            flash('Email address is required.', 'danger')
            return redirect(url_for('auth_routes.reset_password'))

        success, result = get_password_reset_link(email)
        
        if not success:
            flash(result, 'danger')
            return redirect(url_for('auth_routes.reset_password'))

        # Always show the same message whether user exists or not
        flash('If an account exists with this email, a password reset link will be sent.', 'info')
        return redirect(url_for('auth_routes.login'))

    return render_template('reset_password.html')


@auth_routes.route('/auth/handle-reset')
def handle_reset():
    """Handle the password reset link verification."""
    mode = request.args.get('mode')
    oob_code = request.args.get('oobCode')
    
    if mode != 'resetPassword' or not oob_code:
        flash('Invalid password reset link. Please request a new one.', 'danger')
        return redirect(url_for('auth_routes.reset_password'))
        
    try:
        # Verify the action code
        email = auth.verify_password_reset_code(oob_code)
        
        # Store code and email in session for further use
        session['reset_code'] = oob_code
        session['reset_email'] = email
        return redirect(url_for('auth_routes.complete_reset'))
        
    except auth.InvalidIdTokenError:
        flash('This password reset link has expired. Please request a new one.', 'danger')
        return redirect(url_for('auth_routes.reset_password'))
        
    except Exception as e:
        current_app.logger.error(f"Error verifying reset code: {str(e)}")
        flash('Invalid password reset link. Please request a new one.', 'danger')
        return redirect(url_for('auth_routes.reset_password'))


@auth_routes.route('/auth/complete-reset', methods=['GET', 'POST'])
@limiter.limit("5 per minute", key_func=get_auth_rate_limit_key)
def complete_reset():
    """Complete the password reset process by setting a new password."""
    if 'reset_code' not in session or 'reset_email' not in session:
        flash('Your password reset session has expired. Please start over.', 'danger')
        return redirect(url_for('auth_routes.reset_password'))
        
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not new_password or not confirm_password:
            flash('Both password fields are required.', 'danger')
            return redirect(url_for('auth_routes.complete_reset'))
                
        if new_password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return redirect(url_for('auth_routes.complete_reset'))
                
        if len(new_password) < 8 or not re.search(r"[a-z]", new_password) or not re.search(r"[A-Z]", new_password) or not re.search(r"\d", new_password) or not re.search(r"[!@#$%^&*()-_=+{};:,<.>]", new_password):
            flash('Use at least 8 characters with upper & lower case letters, a number, and a symbol.', 'danger')
            return redirect(url_for('auth_routes.complete_reset'))
            
        try:
            # Complete the password reset
            auth.confirm_password_reset(session['reset_code'], new_password)
                
            # Clear session data after successful reset
            session.pop('reset_code', None)
            session.pop('reset_email', None)
                
            flash('Your password has been successfully reset. Please log in with your new password.', 'success')
            return redirect(url_for('auth_routes.login'))
                
        except Exception as e:
            current_app.logger.error(f"Error completing password reset: {str(e)}")
            flash('Failed to reset password. Please try again or request a new reset link.', 'danger')
            return redirect(url_for('auth_routes.reset_password'))
    
    return render_template('complete_reset.html')


@auth_routes.route('/auth/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_auth_rate_limit_key)
def login():
    # Check if user is already logged in
    if 'user' in session:
        flash('You are already logged in.', 'info')
        return redirect(url_for('pages.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        # Validate form inputs
        if not email or not password:
            flash('Both email and password are required.', 'danger')
            return redirect(url_for('auth_routes.login'))

        try:
            # Verify the password using Firebase Auth REST API
            auth_request_data = {
                "email": email,
                "password": password,
                "returnSecureToken": True
            }

            response = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={current_app.config['FIREBASE_WEB_API_KEY']}",
                json=auth_request_data
            )

            response_data = response.json()
            
            if response.status_code == 200:
                # Successfully authenticated, regenerate session
                session.clear()
                session['user'] = _build_session_user(
                    response_data['localId'],
                    response_data['email']
                )
                session.permanent = True
                current_app.permanent_session_lifetime = timedelta(days=7)

                flash('Welcome back! You have successfully logged in.', 'success')
                return redirect(url_for('pages.index'))
            else:
                # Get the error message from the response
                error_message = response_data.get('error', {}).get('message', '')
                
                # Log the error for debugging
                current_app.logger.debug(f"Firebase error response: {error_message}")

                # Handle specific Firebase authentication errors
                if 'INVALID_LOGIN_CREDENTIALS' in error_message:
                    reset_url = url_for('auth_routes.reset_password', _external=True)
                    flash(Markup("Invalid credentials. <a href='{}'>Forgot password?</a>").format(reset_url), 'danger')
                elif 'USER_DISABLED' in error_message:
                    flash('This account has been disabled. Please contact support for assistance.', 'danger')
                elif 'TOO_MANY_ATTEMPTS_TRY_LATER' in error_message:
                    reset_url = url_for('auth_routes.reset_password', _external=True)
                    flash(Markup("Hit the limit. Please wait or <a href='{}' class='alert-link'>Reset</a>").format(reset_url), 'danger')
                else:
                    # Log unexpected errors
                    current_app.logger.error(f"Unexpected Firebase error: {error_message}")
                    flash('Login failed. Please try again later.', 'danger')

                return redirect(url_for('auth_routes.login'))

        except requests.exceptions.RequestException as e:
            current_app.logger.error(f"Network error during login: {str(e)}")
            flash('Network error. Please check your connection and try again.', 'danger')
            return redirect(url_for('auth_routes.login'))

        except Exception as e:
            current_app.logger.error(f"Unexpected login error: {str(e)}")
            flash('An unexpected error occurred. Please try again later.', 'danger')
            return redirect(url_for('auth_routes.login'))

    # Pass OAuth configuration status to the template
    oauth_status = {
        'google': GOOGLE_OAUTH_CONFIGURED,
        'microsoft': MICROSOFT_OAUTH_CONFIGURED,
        'discord': DISCORD_OAUTH_CONFIGURED
    }
    
    return render_template('login.html', oauth_status=oauth_status)


def fetch_options_from_grog_api():
    """
    Fetch options from the GROG API using TaskGenerator.
    Returns a dictionary of options or None if the fetch fails.
    """
    try:
        task_generator = TaskGenerator()
        options = task_generator.generate_task_options()  # Using the correct method name
        if options:
            current_app.logger.info("Successfully fetched options from GROG API")
            return options
        else:
            current_app.logger.warning("No options returned from GROG API, using fallback options")
            return task_generator.get_fallback_options()
    except Exception as e:
        current_app.logger.error(f"Error fetching options from GROG API: {str(e)}")
        return None

def handle_oauth_callback(user_info):
    """
    Common handler for all OAuth callbacks to reduce code duplication
    """
    try:
        # Create or get Firebase user
        user = auth.get_user_by_email(user_info['email'])
    except:
        user = auth.create_user(
            email=user_info['email'],
            display_name=user_info['name']
        )

    # Store user info in Firestore
    db.collection('users').document(user.uid).set({
        'email': user_info['email'],
        'name': user_info['name'],
        'picture': user_info['picture']
    }, merge=True)

    # Store user info in session
    session['user'] = _build_session_user(
        user.uid,
        user_info['email'],
        name=user_info['name'],
        picture=user_info.get('picture')
    )
    session.permanent = True
    current_app.permanent_session_lifetime = timedelta(days=7)

    # Fetch and store options
    try:
        options = fetch_options_from_grog_api()
        if options:
            session['options'] = options
    except Exception as e:
        current_app.logger.error(f"Error handling options: {str(e)}")
        # Continue with login even if options fetch fails

    return redirect(url_for('pages.index'))

# Google OAuth: Redirect to Google OAuth 2.0 consent screen
@auth_routes.route('/auth/google')
def google_login():
    logger.info(f"Google OAuth login requested. Configured: {GOOGLE_OAUTH_CONFIGURED}")
    logger.info(f"Google Client ID: {GOOGLE_CLIENT_ID[:10]}... (Length: {len(GOOGLE_CLIENT_ID) if GOOGLE_CLIENT_ID else 0})")
    
    if not GOOGLE_OAUTH_CONFIGURED:
        flash('Google login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Google OAuth login attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
    
    logger.info("Google OAuth is configured, proceeding with authentication")
    redirect_uri = get_dynamic_redirect_uri('google')
    google_auth_url = (
        f"{GOOGLE_AUTH_URI}?response_type=code"
        f"&client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=openid%20email%20profile"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    logger.info(f"Redirecting to Google OAuth URL: {google_auth_url[:60]}...")
    return redirect(google_auth_url)


@auth_routes.route('/auth/google/callback')
def google_callback():
    if not GOOGLE_OAUTH_CONFIGURED:
        flash('Google login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Google OAuth callback attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
        
    if 'code' not in request.args:
        flash('Google login failed. No authorization code.', 'danger')
        return redirect(url_for('auth_routes.login'))

    code = request.args.get('code')

    # Exchange authorization code for access token
    redirect_uri = get_dynamic_redirect_uri('google')
    token_data = {
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code'
    }

    token_response = requests.post(GOOGLE_TOKEN_URI, data=token_data)
    if token_response.status_code != 200:
        flash('Failed to retrieve tokens from Google.', 'danger')
        return redirect(url_for('auth_routes.login'))

    token_json = token_response.json()
    access_token = token_json.get('access_token')
    id_token = token_json.get('id_token')

    if not id_token:
        flash('Google login failed. No ID token in response.', 'danger')
        return redirect(url_for('auth_routes.login'))

    # Fetch user's profile info using the access token
    headers = {'Authorization': f"Bearer {access_token}"}
    user_info_response = requests.get(GOOGLE_USER_INFO_URI, headers=headers)
    user_info = {}
    if user_info_response.status_code == 200:
        user_info = user_info_response.json()
    else:
        current_app.logger.warning("Google user info request failed: %s", user_info_response.text)

    # Extract user information
    user_email = user_info.get('email')
    user_name = user_info.get('name', 'Google User')
    user_picture = user_info.get('picture')
    if not user_picture:
        try:
            oidc_resp = requests.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers, timeout=5)
            if oidc_resp.status_code == 200:
                user_picture = oidc_resp.json().get('picture')
            else:
                current_app.logger.warning("Google OIDC userinfo failed: %s", oidc_resp.text)
        except Exception as exc:
            current_app.logger.warning("Google OIDC userinfo exception: %s", exc)
    if not user_picture:
        user_picture = url_for('static', filename='images/default-profile.png')

    # Create or link Firebase user
    try:
        user = auth.get_user_by_email(user_email)
    except:
        user = auth.create_user(email=user_email, display_name=user_name)

    # Store user info in Firestore (if applicable)
    db.collection('users').document(user.uid).set({
        'email': user_email,
        'name': user_name,
        'picture': user_picture
    }, merge=True)

    # Store user info in session and make session permanent
    session.clear()
    session['user'] = _build_session_user(user.uid, user_email, name=user_name, picture=user_picture)
    session.permanent = True  # Ensure the session is permanent
    current_app.permanent_session_lifetime = timedelta(days=7)  # Set the session lifetime

    # Fetch options for the index page (if needed)
    try:
        options = fetch_options_from_grog_api()
        if not options:
            flash('Error fetching options. Please try again later.', 'danger')
            return redirect(url_for('pages.index'))  # Redirect to the index page after login

        
        return redirect(url_for('pages.index'))  # Redirect to the index page after login

    except Exception as e:
        flash(f'Error occurred: {str(e)}', 'danger')
        return redirect(url_for('pages.index'))  # Redirect to the index page even on error




# Microsoft OAuth: Redirect to Microsoft OAuth 2.0 consent screen
@auth_routes.route('/auth/microsoft')
def microsoft_login():
    if not MICROSOFT_OAUTH_CONFIGURED:
        flash('Microsoft login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Microsoft OAuth login attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
        
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # Save PKCE and CSRF state in the session
    session['code_verifier'] = code_verifier
    state = str(uuid.uuid4())  # Generate a unique state value for CSRF protection
    session['oauth_state'] = state

    redirect_uri = get_dynamic_redirect_uri('microsoft')
    params = {
        'client_id': MICROSOFT_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': 'user.read openid profile email',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }

    microsoft_auth_url = f"{MICROSOFT_AUTH_URI}?{urlencode(params)}"
    logger.info(f"Redirecting to Microsoft OAuth URL: {microsoft_auth_url}")  # Logging instead of print

    return redirect(microsoft_auth_url)


@auth_routes.route('/auth/microsoft/callback')
def microsoft_callback():
    if not MICROSOFT_OAUTH_CONFIGURED:
        flash('Microsoft login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Microsoft OAuth callback attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
        
    if 'code' not in request.args:
        flash('Microsoft login failed. No authorization code.', 'danger')
        return redirect(url_for('auth_routes.login'))

    code_verifier = session.get('code_verifier')
    if not code_verifier:
        flash('Microsoft login failed. Missing code verifier.', 'danger')
        return redirect(url_for('auth_routes.login'))

    code = request.args.get('code')

    # Exchange authorization code for access token
    redirect_uri = get_dynamic_redirect_uri('microsoft')
    token_data = {
        'client_id': MICROSOFT_CLIENT_ID,
        'client_secret': MICROSOFT_CLIENT_SECRET,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
        'code_verifier': code_verifier
    }

    token_response = requests.post(MICROSOFT_TOKEN_URI, data=token_data)
    if token_response.status_code != 200:
        flash('Failed to retrieve tokens from Microsoft.', 'danger')
        return redirect(url_for('auth_routes.login'))

    token_json = token_response.json()
    access_token = token_json.get('access_token')
    id_token = token_json.get('id_token')

    if not id_token:
        flash('Microsoft login failed. No ID token in response.', 'danger')
        return redirect(url_for('auth_routes.login'))

    # Fetch user's profile info using the access token
    headers = {'Authorization': f"Bearer {access_token}"}
    user_info_response = requests.get(MICROSOFT_USER_INFO_URI, headers=headers)
    user_info = user_info_response.json()

    # Extract user information
    user_email = user_info.get('mail') or user_info.get('userPrincipalName')
    user_name = user_info.get('displayName', 'Microsoft User')
    user_picture = url_for('static', filename='images/default-profile.png')  # Set a default profile picture or update this logic if Microsoft provides a picture URL

    # Create or link Firebase user
    try:
        user = auth.get_user_by_email(user_email)
    except:
        user = auth.create_user(email=user_email, display_name=user_name)

    # Store user info in Firestore (if applicable)
    db.collection('users').document(user.uid).set({
        'email': user_email,
        'name': user_name,
        'picture': user_picture
    }, merge=True)

    # Store user info in session and make session permanent
    session.clear()
    session['user'] = _build_session_user(user.uid, user_email, name=user_name, picture=user_picture)
    session.permanent = True  # Ensure the session is permanent
    current_app.permanent_session_lifetime = timedelta(days=7)  # Set the session lifetime

    # Fetch options for the index page (if needed)
    try:
        options = fetch_options_from_grog_api()
        if not options:
            flash('Error fetching options. Please try again later.', 'danger')
            return redirect(url_for('pages.index'))  # Redirect to the index page after login

        return redirect(url_for('pages.index'))  # Redirect to the index page after login

    except Exception as e:
        flash(f'Error occurred: {str(e)}', 'danger')
        return redirect(url_for('pages.index'))  # Redirect to the index page even on error



# Discord OAuth: Redirect to Discord OAuth 2.0 consent screen
@auth_routes.route('/auth/discord')
def discord_login():
    if not DISCORD_OAUTH_CONFIGURED:
        flash('Discord login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Discord OAuth login attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
    
    # Generate state for CSRF protection
    state = str(uuid.uuid4())
    session['oauth_state'] = state
    
    redirect_uri = get_dynamic_redirect_uri('discord')
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'identify email',
        'state': state
    }
    
    discord_auth_url = f"{DISCORD_AUTH_URI}?{urlencode(params)}"
    return redirect(discord_auth_url)


@auth_routes.route('/auth/discord/callback')
def discord_callback():
    if not DISCORD_OAUTH_CONFIGURED:
        flash('Discord login is not properly configured. Please contact the administrator.', 'danger')
        logger.error("Discord OAuth callback attempted but credentials are not configured")
        return redirect(url_for('auth_routes.login'))
        
    if 'code' not in request.args:
        flash('Discord login failed. No authorization code.', 'danger')
        return redirect(url_for('auth_routes.login'))
    
    # Validate state to prevent CSRF
    state = request.args.get('state')
    stored_state = session.pop('oauth_state', None)
    if not state or state != stored_state:
        flash('Discord login failed. Invalid state parameter.', 'danger')
        return redirect(url_for('auth_routes.login'))

    code = request.args.get('code')

    # Exchange authorization code for access token
    redirect_uri = get_dynamic_redirect_uri('discord')
    token_data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri
    }

    token_response = requests.post(DISCORD_TOKEN_URI, data=token_data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if token_response.status_code != 200:
        flash('Failed to retrieve tokens from Discord.', 'danger')
        return redirect(url_for('auth_routes.login'))

    token_json = token_response.json()
    access_token = token_json.get('access_token')

    # Fetch user's profile info using the access token
    headers = {'Authorization': f"Bearer {access_token}"}
    user_info_response = requests.get(DISCORD_USER_INFO_URI, headers=headers)
    user_info = user_info_response.json()

    # Extract user information
    discord_id = user_info.get('id')
    user_email = user_info.get('email')
    user_name = user_info.get('username', 'Discord User')
    user_picture = f"https://cdn.discordapp.com/avatars/{discord_id}/{user_info.get('avatar')}.png" if user_info.get('avatar') else url_for('static', filename='images/default-profile.png')

    if not user_email:
        flash('Failed to retrieve email from Discord.', 'danger')
        return redirect(url_for('auth_routes.login'))

    # Create or link Firebase user
    try:
        user = auth.get_user_by_email(user_email)
    except:
        user = auth.create_user(email=user_email, display_name=user_name)

    # Store user info in Firestore (if applicable)
    db.collection('users').document(user.uid).set({
        'email': user_email,
        'name': user_name,
        'picture': user_picture
    }, merge=True)

    # Store user info in session and make session permanent
    session.clear()
    session['user'] = _build_session_user(user.uid, user_email, name=user_name, picture=user_picture)
    session.permanent = True  # Ensure the session is permanent
    current_app.permanent_session_lifetime = timedelta(days=7)  # Set the session lifetime

    # Fetch options for the index page (if needed)
    try:
        options = fetch_options_from_grog_api()
        if not options:
            flash('Error fetching options. Please try again later.', 'danger')
            return redirect(url_for('pages.index'))  # Redirect to the index page after login

        return redirect(url_for('pages.index'))  # Redirect to the index page after login

    except Exception as e:
        flash(f'Error occurred: {str(e)}', 'danger')
        return redirect(url_for('pages.index'))  # Redirect to the index page even on error

# Registration route
@auth_routes.route('/auth/register', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_auth_rate_limit_key)
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        if not email:
            flash('Email is required.', 'danger')
            return redirect(url_for('auth_routes.register'))
        if not password:
            flash('Password is required.', 'danger')
            return redirect(url_for('auth_routes.register'))
        if len(password) < 8 or not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password) or not re.search(r"\d", password) or not re.search(r"[!@#$%^&*()-_=+{};:,<.>]", password):
            flash('Use at least 8 characters with upper & lower case letters, a number, and a symbol.', 'danger')
            return redirect(url_for('auth_routes.register'))
        try:
            user = auth.create_user(email=email, password=password)
            # Create a user dictionary for the session, consistent with other login methods
            session.clear() # Regenerate session to prevent fixation
            session['user'] = _build_session_user(
                user.uid,
                user.email,
                name=user.email.split('@')[0],
                picture=url_for('static', filename='images/default-profile.png')
            )
            session.permanent = True
            current_app.permanent_session_lifetime = timedelta(days=7)
            flash('Registration successful. You are now logged in.', 'success')
            return redirect(url_for('auth_routes.dashboard'))
        except Exception as e:
            flash(f'Registration failed. Error: {str(e)}', 'danger')
            return redirect(url_for('auth_routes.register'))
    return render_template('register.html')

@auth_routes.route('/auth/logout', methods=['POST'])
def logout():
    """Log the user out by clearing the session."""
    session.clear()  # Clear all session data
    flash('Logged out! Until next time, adiós!', 'info')
    return redirect(url_for('auth_routes.login'))


def _save_profile_photo(file_storage, user_id: str) -> Optional[str]:
    """Persist a user-uploaded profile image into the static uploads directory."""
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    if not filename:
        raise ValueError("Invalid filename. Please try another image.")

    ext = Path(filename).suffix.lower().lstrip('.')
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        raise ValueError("Unsupported file format. Please upload PNG, JPG, GIF, or WEBP files.")

    upload_dir = Path(current_app.root_path) / 'static' / 'uploads' / 'profile_photos'
    upload_dir.mkdir(parents=True, exist_ok=True)

    final_name = f"{user_id}_{int(time.time())}.{ext}"
    final_path = upload_dir / final_name
    file_storage.save(final_path)

    return url_for('static', filename=f'uploads/profile_photos/{final_name}')


def _build_session_user(uid: str, email: str, name: Optional[str] = None, picture: Optional[str] = None) -> Dict[str, Any]:
    """Merge session defaults with stored profile fields such as show_status."""
    resolved_name = name or (email.split('@')[0] if email else '')
    resolved_picture = picture or url_for('static', filename='images/default-profile.png')
    show_status = True
    try:
        user_doc = db.collection('users').document(uid).get()
        if user_doc.exists:
            data = user_doc.to_dict() or {}
            resolved_name = data.get('name') or resolved_name
            resolved_picture = data.get('picture') or resolved_picture
            show_status = data.get('show_status', True)
    except Exception as exc:
        current_app.logger.warning("Unable to hydrate profile data for %s: %s", uid, exc)

    return {
        'uid': uid,
        'email': email,
        'name': resolved_name,
        'picture': resolved_picture,
        'show_status': show_status
    }


def _build_profile_defaults(user_session: Dict[str, Any], firestore_data: Dict[str, Any]) -> Dict[str, Any]:
    """Hydrate the edit profile form with merged defaults."""
    return {
        'display_name': firestore_data.get('name') or user_session.get('name', ''),
        'username': firestore_data.get('username') or '',
        'job_title': firestore_data.get('job_title') or '',
        'team': firestore_data.get('team') or '',
        'timezone': firestore_data.get('timezone') or 'UTC',
        'location': firestore_data.get('location') or '',
        'website': firestore_data.get('website') or '',
        'bio': firestore_data.get('bio') or '',
        'show_status': firestore_data.get('show_status', True),
        'product_updates': firestore_data.get('product_updates', True),
        'security_alerts': firestore_data.get('security_alerts', True),
        'picture': firestore_data.get('picture') or user_session.get('picture'),
    }


@auth_routes.route('/profile/edit', methods=['GET', 'POST'])
def edit_profile():
    if 'user' not in session:
        flash('Please log in to edit your profile.', 'warning')
        return redirect(url_for('auth_routes.login'))

    user_data = session['user']
    profile_snapshot = db.collection('users').document(user_data['uid']).get()
    profile_doc = profile_snapshot.to_dict() if profile_snapshot.exists else {}
    form_defaults = _build_profile_defaults(user_data, profile_doc or {})

    timezones = [
        ("UTC", "UTC"),
        ("America/Los_Angeles", "Pacific (PT)"),
        ("America/Denver", "Mountain (MT)"),
        ("America/Chicago", "Central (CT)"),
        ("America/New_York", "Eastern (ET)"),
        ("Europe/London", "London"),
        ("Europe/Berlin", "Central Europe"),
        ("Asia/Tokyo", "Tokyo"),
        ("Australia/Sydney", "Sydney"),
    ]

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        username = request.form.get('username', '').strip()
        job_title = request.form.get('job_title', '').strip()
        team = request.form.get('team', '').strip()
        timezone_value = request.form.get('timezone', 'UTC')
        location = request.form.get('location', '').strip()
        website = request.form.get('website', '').strip()
        bio = request.form.get('bio', '').strip()
        show_status = bool(request.form.get('show_status'))
        product_updates = bool(request.form.get('product_updates'))
        security_alerts = bool(request.form.get('security_alerts'))
        profile_photo = request.files.get('profile_photo')

        updates: Dict[str, Any] = {
            'name': display_name or user_data.get('name'),
            'username': username,
            'job_title': job_title,
            'team': team,
            'timezone': timezone_value,
            'location': location,
            'website': website,
            'bio': bio,
            'show_status': show_status,
            'product_updates': product_updates,
            'security_alerts': security_alerts,
        }

        try:
            new_photo_url = _save_profile_photo(profile_photo, user_data['uid'])
        except ValueError as exc:
            flash(str(exc), 'danger')
            return redirect(url_for('auth_routes.edit_profile'))

        if new_photo_url:
            updates['picture'] = new_photo_url
            session['user']['picture'] = new_photo_url
            form_defaults['picture'] = new_photo_url

        db.collection('users').document(user_data['uid']).set(updates, merge=True)
        if display_name:
            session['user']['name'] = display_name
        session['user']['show_status'] = show_status

        form_defaults.update(updates)
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('auth_routes.edit_profile'))

    return render_template(
        'profile_edit.html',
        user_data=user_data,
        form_defaults=form_defaults,
        timezones=timezones
    )

@auth_routes.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash('Please log in to access the dashboard.', 'warning')
        return redirect(url_for('auth_routes.login'))

    user_data = session['user']

    # --- Robust session data check ---
    if not isinstance(user_data, dict) or 'uid' not in user_data or 'email' not in user_data:
        current_app.logger.warning(f"Invalid or incomplete user_data in session: {user_data}. Redirecting to login.")
        session.clear() # Clear potentially corrupt session
        flash('Your session data is invalid or incomplete. Please log in again.', 'danger')
        return redirect(url_for('auth_routes.login'))
    # --- End of robust session data check ---

    # Fetch real user data if available and generate mock data for stats
    active_model_label = session.get('selected_model_label', 'Llama3.3')

    user_sessions_ref = db.collection('users').document(user_data['uid']).collection('chat_sessions')
    recent_sessions_docs: list = []
    try:
        recent_sessions_docs = list(
            user_sessions_ref
            .order_by('last_updated', direction=gc_firestore.Query.DESCENDING)
            .limit(6)
            .stream()
        )
    except Exception as e:
        current_app.logger.warning(f"Unable to order sessions for user {user_data['uid']}: {e}")
        recent_sessions_docs = list(user_sessions_ref.limit(6).stream())

    def _format_timestamp(ts_value):
        if isinstance(ts_value, datetime):
            return ts_value.strftime('%b %d, %H:%M')
        if isinstance(ts_value, str):
            try:
                parsed = datetime.fromisoformat(ts_value.replace('Z', '+00:00'))
                return parsed.strftime('%b %d, %H:%M')
            except Exception:
                return ts_value
        return 'Just now'

    def _format_size(size_bytes):
        try:
            size = int(size_bytes)
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024:
                    return f"{size:.0f} {unit}"
                size /= 1024
            return f"{size:.1f} TB"
        except Exception:
            return '--'

    recent_sessions = []
    files_feed = []
    messages_sent = 0
    total_files_count = 0

    for doc in recent_sessions_docs:
        data = doc.to_dict() or {}
        messages = data.get('messages') or []
        messages_sent += len(messages)
        last_msg = messages[-1] if messages else {}
        preview = last_msg.get('prompt') or last_msg.get('response') or 'New conversation'
        updated_at = data.get('last_updated') or data.get('created_at')
        recent_sessions.append({
            'id': doc.id,
            'name': data.get('session_name') or 'Untitled session',
            'updated_at': _format_timestamp(updated_at),
            'preview': preview[:120] + ('…' if len(preview) > 120 else ''),
            'has_files': bool(data.get('files')),
            'message_count': len(messages)
        })

        for file_entry in (data.get('files') or []):
            total_files_count += 1
            if len(files_feed) < 5:
                files_feed.append({
                    'name': file_entry.get('original_filename') or file_entry.get('filename') or 'Attachment',
                    'size': _format_size(file_entry.get('file_size', 0)),
                    'uploaded_at': _format_timestamp(file_entry.get('upload_date')),
                    'session_name': data.get('session_name') or 'Untitled session'
                })

    try:
        user_profile_doc = db.collection('users').document(user_data['uid']).get()
        user_profile_data = user_profile_doc.to_dict() if user_profile_doc.exists else {}
    except Exception:
        user_profile_data = {}

    stored_total_sessions = user_profile_data.get('total_sessions')
    if stored_total_sessions is not None:
        total_chats = stored_total_sessions
    else:
        try:
            total_chats = sum(1 for _ in user_sessions_ref.stream())
        except Exception:
            total_chats = len(recent_sessions)
        else:
            try:
                db.collection('users').document(user_data['uid']).set(
                    {'total_sessions': total_chats},
                    merge=True
                )
            except Exception:
                pass

    files_uploaded = total_files_count

    credits_used = user_profile_data.get('credits_used')
    if credits_used is None:
        credits_used = max(messages_sent * 20, 0)

    usage_summary = {
        'total_chats': total_chats,
        'files_uploaded': files_uploaded,
        'messages_sent': messages_sent,
        'model_label': active_model_label
    }

    quick_actions = [
        {
            'title': 'Start fresh chat',
            'description': 'Blank canvas with your default model',
            'icon': 'fas fa-plus-circle',
            'href': url_for('pages.index')
        },
        {
            'title': 'Upload files',
            'description': 'Ground responses in your docs',
            'icon': 'fas fa-file-upload',
            'href': '#upload-modal'
        },
        {
            'title': 'View history',
            'description': 'Browse all past conversations',
            'icon': 'fas fa-history',
            'href': url_for('pages.index') + '#history'
        }
    ]

    account_age = "N/A"
    try:
        user_record = auth.get_user(user_data['uid'])
        if user_record and user_record.user_metadata and user_record.user_metadata.creation_timestamp:
            creation_datetime_ms = user_record.user_metadata.creation_timestamp
            # Convert milliseconds to datetime object
            creation_datetime = datetime.fromtimestamp(creation_datetime_ms / 1000)
            age_delta = datetime.now() - creation_datetime
            account_age = f"{age_delta.days} days old"
        else:
            current_app.logger.warning(f"User {user_data['uid']} has no creation_timestamp metadata.")
            account_age = f"{random.randint(1, 365)} days old" # Fallback random age
    except Exception as e:
        current_app.logger.error(f"Error fetching user metadata for {user_data['uid']}: {str(e)}")
        account_age = f"{random.randint(1, 365)} days old" # Fallback random age


    # Generate random data for activity feed
    activity_types = [
        ("fas fa-comment", "New chat started: {item}", "chat"),
        ("fas fa-user-edit", "Your profile name was updated.", "profile"),
        ("fas fa-file-upload", "Uploaded file: {item}", "file"),
        ("fas fa-cog", "Account settings were updated.", "setting"),
    ]
    recent_activities = []
    for _ in range(random.randint(3, 6)): # Between 3 and 6 random activities
        icon, desc_template, type_class = random.choice(activity_types)
        item_name = ""
        if "chat" in type_class:
            item_name = random.choice(["Project Alpha", "Marketing Review", "New Feature Ideas", "Daily Standup"])
        elif "file" in type_class:
            item_name = random.choice(["report.pdf", "image.jpg", "document.docx"])

        description = desc_template.format(item=item_name) if item_name else desc_template
        timestamp_delta = random.randint(1, 72) # hours ago
        
        # Simulate realistic timestamps (e.g., "X hours/days ago")
        if timestamp_delta < 24:
            timestamp_str = f"{timestamp_delta}h ago"
        else:
            timestamp_str = f"{timestamp_delta // 24}d ago"

        recent_activities.append({
            'icon_class': icon,
            'description': description,
            'timestamp': timestamp_str,
            'type_class': type_class
        })
    
    # Generate random data for the chart
    chart_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    chart_data = [random.randint(1, 20) for _ in range(7)]

    # Pass user data and generated stats/activities to the template
    notifications = [{
        'icon': item['icon_class'],
        'text': item['description'],
        'time': item['timestamp']
    } for item in recent_activities]

    return render_template(
        'dashboard.html',
        user_data=user_data,
        usage_summary=usage_summary,
        quick_actions=quick_actions,
        recent_sessions=recent_sessions,
        files_feed=files_feed,
        notifications=notifications,
        account_age=account_age,
        credits_used=credits_used,
        active_model_label=active_model_label,
        model_labels=MODEL_LABELS,
        chart_labels=chart_labels,
        chart_data=chart_data,
        total_chats=total_chats,
        files_uploaded=files_uploaded
    )
