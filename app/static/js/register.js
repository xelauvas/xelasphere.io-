(() => {
    const togglePassword = document.querySelector('.toggle-password');
    const passwordField = document.querySelector('#password');
    const eyeIcon = document.querySelector('#eye-icon');

    if (togglePassword && passwordField && eyeIcon) {
        togglePassword.addEventListener('click', () => {
            const isPassword = passwordField.getAttribute('type') === 'password';
            passwordField.setAttribute('type', isPassword ? 'text' : 'password');
            eyeIcon.innerHTML = isPassword
                ? '<path fill="#a1a1a1" d="M572.52 241.4C518.7 135.5 407.1 64 288 64S57.3 135.5 3.48 241.4a48.43 48.43 0 000 29.2C57.3 376.5 168.9 448 288 448s230.7-71.5 284.52-177.4a48.43 48.43 0 000-29.2zM288 400c-97.2 0-176-78.8-176-176s78.8-176 176-176 176 78.8 176 176-78.8 176-176 176zm0-304a128 128 0 10128 128A128 128 0 00288 96z" />'
                : '<path fill="#a1a1a1" d="M572.52 241.4C518.7 135.5 407.1 64 288 64S57.3 135.5 3.48 241.4a48.43 48.43 0 000 29.2C57.3 376.5 168.9 448 288 448s230.7-71.5 284.52-177.4a48.43 48.43 0 000-29.2zM288 400c-97.2 0-176-78.8-176-176s78.8-176 176-176 176 78.8 176 176-78.8 176-176 176zm0-304a128 128 0 10128 128A128 128 0 00288 96z" />';
        });
    }

    if (typeof particlesJS !== 'undefined') {
        particlesJS.load('particle-canvas', 'particles.json', () => {
            console.log('Particles.js loaded - callback');
        });
    }

    const passwordInput = document.querySelector('#password');
    const strengthBar = document.querySelector('#password-strength span');

    if (passwordInput && strengthBar) {
        passwordInput.addEventListener('input', () => {
            const value = passwordInput.value || '';
            let strength = 0;

            if (value.length >= 8) strength += 1;
            if (/[A-Z]/.test(value)) strength += 1;
            if (/[a-z]/.test(value)) strength += 1;
            if (/[0-9]/.test(value)) strength += 1;
            if (/[^A-Za-z0-9]/.test(value)) strength += 1;

            switch (strength) {
                case 0:
                    strengthBar.style.width = '0%';
                    break;
                case 1:
                    strengthBar.style.width = '20%';
                    strengthBar.style.backgroundColor = '#ff4d4d';
                    break;
                case 2:
                    strengthBar.style.width = '40%';
                    strengthBar.style.backgroundColor = '#ff944d';
                    break;
                case 3:
                    strengthBar.style.width = '60%';
                    strengthBar.style.backgroundColor = '#ffff4d';
                    break;
                case 4:
                    strengthBar.style.width = '80%';
                    strengthBar.style.backgroundColor = '#94ff4d';
                    break;
                case 5:
                    strengthBar.style.width = '100%';
                    strengthBar.style.backgroundColor = '#4dff88';
                    break;
                default:
                    strengthBar.style.width = '0%';
            }
        });
    }
})();
