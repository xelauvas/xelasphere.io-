"""Reminders CRUD blueprint. M0: email-only, login-required."""
import logging
from flask import Blueprint, jsonify

from app.blueprints.shared import login_required, error_handler

logger = logging.getLogger(__name__)

reminders_bp = Blueprint("reminders", __name__)


@reminders_bp.route("/reminders", methods=["GET"])
@error_handler
@login_required
def list_reminders():
    """Stub — returns empty list. Real impl in Task 1.4."""
    return jsonify({"reminders": []})
