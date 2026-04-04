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
                : '<path fill="#a1a1a1" d="M286.17 272a80 80 0 00-89.74-89.21C167.1 196.26 144 233.37 144 276a128 128 0 00227.07 81.31 176.24 176.24 0 01-84.9-85.31zm247.41 74.91C505.3 310.6 480 269.21 480 224c0-133.06-114.13-240-256-240C124.33-16 28.73 51.59 3.48 148.41a48.43 48.43 0 000 29.18C28.73 274.41 124.33 342 224 342a303.27 303.27 0 0086.15-12.39l47.11 47.11a16 16 0 0022.63 0l26.48-26.48a16 16 0 000-22.63zM224 296a72 72 0 1172-72 72.08 72.08 0 01-72 72z" /><line x1="1" y1="1" x2="23" y2="23" stroke="#a1a1a1" stroke-width="40"/>';
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
