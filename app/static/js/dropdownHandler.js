(() => {
    function toggleDropdown(dropdownContainer) {
        dropdownContainer.classList.toggle('active');
        const isOpen = dropdownContainer.classList.contains('active');
        const profileHeader = dropdownContainer.closest('.profile-header');
        if (profileHeader) {
            profileHeader.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        }
        if (isOpen) {
            document.addEventListener('click', handleClickOutside);
            document.addEventListener('keydown', handleEscape);
        } else {
            document.removeEventListener('click', handleClickOutside);
            document.removeEventListener('keydown', handleEscape);
        }
    }

    function handleClickOutside(event) {
        const dropdownContainer = document.querySelector('.sidebar-footer .dropdown-container');
        const profileHeader = document.querySelector('.sidebar-footer .profile-header');
        if (!dropdownContainer || !profileHeader) return;

        if (!dropdownContainer.contains(event.target) && !profileHeader.contains(event.target)) {
            closeDropdown(dropdownContainer);
        }
    }

    function handleEscape(event) {
        if (event.key !== 'Escape') return;
        const dropdownContainer = document.querySelector('.sidebar-footer .dropdown-container.active');
        if (dropdownContainer) closeDropdown(dropdownContainer);
    }

    function closeDropdown(dropdownContainer) {
        dropdownContainer.classList.remove('active');
        const profileHeader = dropdownContainer.closest('.profile-header');
        if (profileHeader) profileHeader.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', handleClickOutside);
        document.removeEventListener('keydown', handleEscape);
    }

    document.addEventListener('DOMContentLoaded', () => {
        const profileHeader = document.querySelector('.sidebar-footer .profile-header');
        const dropdownContainer = document.querySelector('.sidebar-footer .dropdown-container');
        if (!profileHeader || !dropdownContainer) return;

        profileHeader.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleDropdown(dropdownContainer);
        });

        profileHeader.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.stopPropagation();
                toggleDropdown(dropdownContainer);
            }
        });
    });
})();
