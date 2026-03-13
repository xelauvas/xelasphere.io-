(() => {
    function toggleDropdown(dropdownMenu) {
        dropdownMenu.classList.toggle('active');
        if (dropdownMenu.classList.contains('active')) {
            document.addEventListener('click', handleClickOutside);
        } else {
            document.removeEventListener('click', handleClickOutside);
        }
    }

    function handleClickOutside(event) {
        const dropdownMenu = document.querySelector('.dropdown-menu');
        const profileHeader = document.querySelector('.profile-header');
        if (!dropdownMenu || !profileHeader) return;

        if (!dropdownMenu.contains(event.target) && !profileHeader.contains(event.target)) {
            dropdownMenu.classList.remove('active');
            document.removeEventListener('click', handleClickOutside);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const profileHeader = document.querySelector('.profile-header');
        const dropdownMenu = document.querySelector('.dropdown-menu');
        if (!profileHeader || !dropdownMenu) return;

        profileHeader.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleDropdown(dropdownMenu);
        });
    });
})();
