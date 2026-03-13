/**
 * File Preview System
 * Modern file thumbnails and preview modal for chat attachments
 */

const FilePreview = (function() {
    'use strict';

    // File type to icon mapping (Font Awesome icons)
    const FILE_ICONS = {
        // Documents
        pdf: 'fas fa-file-pdf',
        doc: 'fas fa-file-word',
        docx: 'fas fa-file-word',
        xls: 'fas fa-file-excel',
        xlsx: 'fas fa-file-excel',
        csv: 'fas fa-file-csv',
        ppt: 'fas fa-file-powerpoint',
        pptx: 'fas fa-file-powerpoint',
        rtf: 'fas fa-file-alt',

        // Text & Markup
        txt: 'fas fa-file-alt',
        md: 'fab fa-markdown',
        html: 'fab fa-html5',
        htm: 'fab fa-html5',
        xml: 'fas fa-file-code',
        svg: 'fas fa-bezier-curve',

        // Code
        js: 'fab fa-js',
        ts: 'fas fa-file-code',
        jsx: 'fab fa-react',
        tsx: 'fab fa-react',
        py: 'fab fa-python',
        java: 'fab fa-java',
        c: 'fas fa-file-code',
        cpp: 'fas fa-file-code',
        cs: 'fas fa-file-code',
        go: 'fas fa-file-code',
        rs: 'fas fa-file-code',
        rb: 'fas fa-gem',
        php: 'fab fa-php',
        swift: 'fab fa-swift',
        kt: 'fas fa-file-code',
        scala: 'fas fa-file-code',

        // Config & Data
        json: 'fas fa-brackets-curly',
        yaml: 'fas fa-file-code',
        yml: 'fas fa-file-code',
        toml: 'fas fa-file-code',
        ini: 'fas fa-cog',
        cfg: 'fas fa-cog',
        env: 'fas fa-key',

        // Styles
        css: 'fab fa-css3-alt',
        scss: 'fab fa-sass',
        sass: 'fab fa-sass',
        less: 'fas fa-file-code',

        // Shell & Scripts
        sh: 'fas fa-terminal',
        bash: 'fas fa-terminal',
        zsh: 'fas fa-terminal',
        ps1: 'fas fa-terminal',
        bat: 'fas fa-terminal',

        // Images
        png: 'fas fa-image',
        jpg: 'fas fa-image',
        jpeg: 'fas fa-image',
        gif: 'fas fa-image',
        webp: 'fas fa-image',
        ico: 'fas fa-image',

        // Archives
        zip: 'fas fa-file-archive',
        rar: 'fas fa-file-archive',
        tar: 'fas fa-file-archive',
        gz: 'fas fa-file-archive',
        '7z': 'fas fa-file-archive',

        // Audio
        mp3: 'fas fa-file-audio',
        wav: 'fas fa-file-audio',
        ogg: 'fas fa-file-audio',
        flac: 'fas fa-file-audio',

        // Video
        mp4: 'fas fa-file-video',
        webm: 'fas fa-file-video',
        avi: 'fas fa-file-video',
        mov: 'fas fa-file-video',
        mkv: 'fas fa-file-video',

        // Database
        sql: 'fas fa-database',
        db: 'fas fa-database',
        sqlite: 'fas fa-database',

        // Other
        log: 'fas fa-scroll',
        dockerfile: 'fab fa-docker',
        makefile: 'fas fa-cogs',
        gradle: 'fas fa-cogs',

        // Default
        default: 'fas fa-file'
    };

    // File type categories for styling
    const FILE_CATEGORIES = {
        pdf: 'pdf',
        doc: 'doc', docx: 'docx',
        xls: 'xls', xlsx: 'xlsx', csv: 'csv',
        ppt: 'ppt', pptx: 'pptx',
        html: 'html', htm: 'html',
        css: 'css', scss: 'css', sass: 'css', less: 'css',
        js: 'js', mjs: 'js', cjs: 'js',
        json: 'json',
        md: 'md', txt: 'txt',
        png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image',
        zip: 'zip', rar: 'archive', tar: 'archive', gz: 'archive', '7z': 'archive',
        mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio',
        mp4: 'video', webm: 'video', avi: 'video', mov: 'video', mkv: 'video'
    };

    // Extensions that can be previewed as text/code
    const TEXT_PREVIEWABLE = new Set([
        'txt', 'md', 'html', 'htm', 'xml', 'svg', 'css', 'scss', 'sass', 'less',
        'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'json', 'yaml', 'yml', 'toml',
        'py', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'go', 'rs', 'rb', 'php',
        'swift', 'kt', 'scala', 'sh', 'bash', 'zsh', 'ps1', 'sql', 'ini', 'cfg',
        'conf', 'env', 'log', 'dockerfile', 'makefile', 'gradle', 'cmake',
        'vue', 'svelte', 'rtf', 'csv'
    ]);

    // Image extensions
    const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp']);

    let modalElement = null;

    /**
     * Get file extension from filename
     */
    function getExtension(filename) {
        if (!filename) return '';
        const parts = filename.split('.');
        return parts.length > 1 ? parts.pop().toLowerCase() : '';
    }

    /**
     * Get icon class for file type
     */
    function getFileIcon(filename) {
        const ext = getExtension(filename);
        return FILE_ICONS[ext] || FILE_ICONS.default;
    }

    /**
     * Get file category for styling
     */
    function getFileCategory(filename) {
        const ext = getExtension(filename);
        return FILE_CATEGORIES[ext] || 'code';
    }

    /**
     * Check if file is an image
     */
    function isImage(filename, mimeType) {
        if (mimeType && mimeType.startsWith('image/')) return true;
        const ext = getExtension(filename);
        return IMAGE_EXTENSIONS.has(ext);
    }

    /**
     * Check if file can be previewed as text
     */
    function isTextPreviewable(filename) {
        const ext = getExtension(filename);
        return TEXT_PREVIEWABLE.has(ext);
    }

    /**
     * Format file size
     */
    function formatFileSize(bytes) {
        if (!bytes || bytes === 0) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
    }

    /**
     * Create a file preview card element
     */
    function createFileCard(attachment, options = {}) {
        const {
            onClick = null,
            showMeta = true
        } = options;

        const filename = attachment.original_filename || attachment.name || attachment.filename || 'Unknown file';
        const ext = getExtension(filename);
        const category = getFileCategory(filename);
        const icon = getFileIcon(filename);
        const fileSize = attachment.file_size || attachment.size;
        const url = resolveUrl(attachment);
        const isImg = isImage(filename, attachment.mime_type);

        const card = document.createElement('div');
        card.className = 'file-preview-card';
        card.dataset.fileType = category;
        card.dataset.filename = filename;
        card.dataset.url = url || '';

        // Thumbnail area
        const thumb = document.createElement('div');
        thumb.className = 'file-preview-card__thumb';

        if (isImg && url) {
            const img = document.createElement('img');
            img.src = url;
            img.alt = filename;
            img.loading = 'lazy';
            // Blur-up loading effect - add loaded class when image is ready
            img.onload = () => {
                img.classList.add('loaded');
                thumb.classList.add('loaded');
            };
            img.onerror = () => {
                // Replace with icon on error
                thumb.innerHTML = `<div class="file-preview-card__icon"><i class="${icon}"></i></div>`;
            };
            // If image is already cached, trigger loaded immediately
            if (img.complete && img.naturalHeight !== 0) {
                img.classList.add('loaded');
                thumb.classList.add('loaded');
            }
            thumb.appendChild(img);
        } else {
            const iconDiv = document.createElement('div');
            iconDiv.className = 'file-preview-card__icon';
            iconDiv.innerHTML = `<i class="${icon}"></i>`;
            thumb.appendChild(iconDiv);
        }

        // Hover overlay
        const overlay = document.createElement('div');
        overlay.className = 'file-preview-card__overlay';
        overlay.innerHTML = `<div class="file-preview-card__overlay-icon"><i class="fas fa-eye"></i></div>`;
        thumb.appendChild(overlay);

        card.appendChild(thumb);

        // File info
        if (showMeta) {
            const info = document.createElement('div');
            info.className = 'file-preview-card__info';

            const name = document.createElement('div');
            name.className = 'file-preview-card__name';
            name.textContent = filename;
            name.title = filename;
            info.appendChild(name);

            const meta = document.createElement('div');
            meta.className = 'file-preview-card__meta';

            if (ext) {
                const extBadge = document.createElement('span');
                extBadge.className = 'file-preview-card__ext';
                extBadge.textContent = ext;
                meta.appendChild(extBadge);
            }

            if (fileSize) {
                const size = document.createElement('span');
                size.className = 'file-preview-card__size';
                size.textContent = formatFileSize(fileSize);
                meta.appendChild(size);
            }

            info.appendChild(meta);
            card.appendChild(info);
        }

        // Click handler
        card.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (onClick) {
                onClick(attachment);
            } else {
                openPreviewModal(attachment);
            }
        });

        return card;
    }

    /**
     * Resolve attachment URL
     */
    function resolveUrl(attachment) {
        if (attachment.dataUrl) return attachment.dataUrl;
        if (attachment.url) {
            if (attachment.url.startsWith('data:') || attachment.url.startsWith('http')) {
                return attachment.url;
            }
            return attachment.url.startsWith('/') ? attachment.url : '/' + attachment.url;
        }
        if (attachment.relative_path) {
            return '/uploads/' + attachment.relative_path;
        }
        if (attachment.path) {
            return attachment.path.startsWith('/') ? attachment.path : '/uploads/' + attachment.path;
        }
        return null;
    }

    /**
     * Create and inject the preview modal into DOM
     */
    function createModal() {
        if (modalElement) return modalElement;

        modalElement = document.createElement('div');
        modalElement.className = 'file-preview-modal';
        modalElement.id = 'file-preview-modal';
        modalElement.innerHTML = `
            <div class="file-preview-modal__container">
                <div class="file-preview-modal__header">
                    <div class="file-preview-modal__title">
                        <i class="file-preview-modal__title-icon fas fa-file"></i>
                        <span class="file-preview-modal__title-text">File Preview</span>
                    </div>
                    <div class="file-preview-modal__actions">
                        <button class="file-preview-modal__btn file-preview-modal__download" title="Download">
                            <i class="fas fa-download"></i>
                        </button>
                        <button class="file-preview-modal__btn file-preview-modal__close" title="Close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
                <div class="file-preview-modal__content"></div>
                <div class="file-preview-modal__footer">
                    <div class="file-preview-modal__footer-meta">
                        <span class="file-preview-modal__footer-type"></span>
                        <span class="file-preview-modal__footer-size"></span>
                    </div>
                </div>
            </div>
        `;

        // Close handlers
        modalElement.addEventListener('click', (e) => {
            if (e.target === modalElement) closePreviewModal();
        });

        modalElement.querySelector('.file-preview-modal__close').addEventListener('click', closePreviewModal);

        // Download handler
        modalElement.querySelector('.file-preview-modal__download').addEventListener('click', () => {
            const url = modalElement.dataset.currentUrl;
            const filename = modalElement.dataset.currentFilename;
            if (url) {
                const a = document.createElement('a');
                a.href = url;
                a.download = filename || 'download';
                a.click();
            }
        });

        // ESC key to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modalElement.classList.contains('open')) {
                closePreviewModal();
            }
        });

        document.body.appendChild(modalElement);
        return modalElement;
    }

    /**
     * Open the preview modal for a file
     */
    async function openPreviewModal(attachment) {
        const modal = createModal();
        const content = modal.querySelector('.file-preview-modal__content');
        const titleIcon = modal.querySelector('.file-preview-modal__title-icon');
        const titleText = modal.querySelector('.file-preview-modal__title-text');
        const footerType = modal.querySelector('.file-preview-modal__footer-type');
        const footerSize = modal.querySelector('.file-preview-modal__footer-size');

        const filename = attachment.original_filename || attachment.name || attachment.filename || 'Unknown file';
        const ext = getExtension(filename);
        const icon = getFileIcon(filename);
        const url = resolveUrl(attachment);
        const mimeType = attachment.mime_type || '';

        // Store for download
        modal.dataset.currentUrl = url || '';
        modal.dataset.currentFilename = filename;

        // Update header
        titleIcon.className = 'file-preview-modal__title-icon ' + icon;
        titleText.textContent = filename;

        // Update footer
        footerType.textContent = ext.toUpperCase() || 'Unknown';
        footerSize.textContent = formatFileSize(attachment.file_size || attachment.size);

        // Clear previous content
        content.innerHTML = '';

        // Render content based on file type
        if (isImage(filename, mimeType) && url) {
            const img = document.createElement('img');
            img.className = 'file-preview-modal__image';
            img.src = url;
            img.alt = filename;
            content.appendChild(img);
        } else if (ext === 'pdf' && url) {
            // PDF preview - use object/embed for better compatibility
            const iframe = document.createElement('iframe');
            iframe.className = 'file-preview-modal__pdf';
            iframe.src = url;
            content.appendChild(iframe);
        } else if (isTextPreviewable(filename) && url) {
            // Fetch and display text content
            content.innerHTML = '<div class="file-preview-modal__code" style="opacity:0.5">Loading...</div>';
            try {
                const response = await fetch(url);
                if (response.ok) {
                    const text = await response.text();
                    const pre = document.createElement('pre');
                    pre.className = 'file-preview-modal__code';
                    pre.textContent = text.substring(0, 50000); // Limit preview size
                    if (text.length > 50000) {
                        pre.textContent += '\n\n... [truncated - file too large for preview]';
                    }
                    content.innerHTML = '';
                    content.appendChild(pre);
                } else {
                    throw new Error('Failed to load file');
                }
            } catch (e) {
                content.innerHTML = `
                    <div class="file-preview-modal__unsupported">
                        <i class="file-preview-modal__unsupported-icon fas fa-exclamation-triangle"></i>
                        <div class="file-preview-modal__unsupported-text">Failed to load preview</div>
                        <div class="file-preview-modal__unsupported-hint">Click download to get the file</div>
                    </div>
                `;
            }
        } else {
            // Unsupported file type
            content.innerHTML = `
                <div class="file-preview-modal__unsupported">
                    <i class="file-preview-modal__unsupported-icon ${icon}"></i>
                    <div class="file-preview-modal__unsupported-text">Preview not available for this file type</div>
                    <div class="file-preview-modal__unsupported-hint">Click download to get the file</div>
                </div>
            `;
        }

        // Show modal
        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    /**
     * Close the preview modal
     */
    function closePreviewModal() {
        if (modalElement) {
            modalElement.classList.remove('open');
            document.body.style.overflow = '';
            // Clear content after animation
            setTimeout(() => {
                const content = modalElement.querySelector('.file-preview-modal__content');
                if (content) content.innerHTML = '';
            }, 300);
        }
    }

    /**
     * Render attachments as file preview cards
     */
    function renderAttachments(attachments, container, options = {}) {
        if (!attachments || !attachments.length || !container) return;

        container.innerHTML = '';
        attachments.forEach(att => {
            const card = createFileCard(att, options);
            container.appendChild(card);
        });
    }

    // Public API
    return {
        createFileCard,
        renderAttachments,
        openPreviewModal,
        closePreviewModal,
        getFileIcon,
        getFileCategory,
        getExtension,
        formatFileSize,
        isImage,
        isTextPreviewable,
        resolveUrl
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = FilePreview;
}
