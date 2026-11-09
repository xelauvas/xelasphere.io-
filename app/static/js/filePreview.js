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
    let currentObjectUrl = null;
    const pdfThumbnailCache = new Map();

    function clearObjectUrl() {
        if (currentObjectUrl) {
            URL.revokeObjectURL(currentObjectUrl);
            currentObjectUrl = null;
        }
    }

    function setPdfPageCount(card, meta, pageCount) {
        if (!card || !meta || !pageCount) return;
        card.dataset.pdfPages = String(pageCount);
        let pageEl = meta.querySelector('.file-preview-card__pages');
        if (!pageEl) {
            pageEl = document.createElement('span');
            pageEl.className = 'file-preview-card__pages';
            const actionEl = meta.querySelector('.file-preview-card__action');
            meta.insertBefore(pageEl, actionEl || null);
        }
        pageEl.textContent = `${pageCount} page${pageCount === 1 ? '' : 's'}`;
    }

    function renderPdfThumbnail(url, thumb, card = null, meta = null) {
        if (!url || !thumb || !window.pdfjsLib || !window.pdfjsLib.getDocument) return;

        const applyResult = (result) => {
            const src = typeof result === 'string' ? result : result?.dataUrl;
            if (!src || !thumb.isConnected) return;
            const img = document.createElement('img');
            img.className = 'file-preview-card__pdf-thumb-img';
            img.src = src;
            img.alt = 'PDF preview';
            thumb.querySelector('.file-preview-card__icon')?.remove();
            thumb.querySelectorAll('.file-preview-card__pdf-lines, .file-preview-card__pdf-ribbon').forEach(el => el.remove());
            thumb.appendChild(img);
            thumb.classList.add('loaded', 'has-pdf-thumbnail');
            card?.classList.add('has-pdf-thumbnail');
            setPdfPageCount(card, meta, result?.pageCount);
        };

        if (pdfThumbnailCache.has(url)) {
            const cached = pdfThumbnailCache.get(url);
            if (typeof cached === 'string' || cached?.dataUrl) {
                applyResult(cached);
            } else if (cached && typeof cached.then === 'function') {
                cached.then(applyResult).catch(() => {});
            }
            return;
        }

        const task = fetch(url, { credentials: 'same-origin' })
            .then(response => {
                if (!response.ok) throw new Error('PDF thumbnail fetch failed');
                return response.arrayBuffer();
            })
            .then(async buffer => {
                const pdf = await window.pdfjsLib.getDocument({
                    data: buffer,
                    disableWorker: true
                }).promise;
                const pageCount = pdf.numPages || 0;
                const page = await pdf.getPage(1);
                const baseViewport = page.getViewport({ scale: 1 });
                const targetWidth = 140;
                const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
                const cssScale = targetWidth / baseViewport.width;
                const viewport = page.getViewport({ scale: cssScale * pixelRatio });
                const canvas = document.createElement('canvas');
                const context = canvas.getContext('2d');
                canvas.width = Math.floor(viewport.width);
                canvas.height = Math.floor(viewport.height);
                await page.render({ canvasContext: context, viewport }).promise;
                const dataUrl = canvas.toDataURL('image/jpeg', 0.82);
                const result = { dataUrl, pageCount };
                pdfThumbnailCache.set(url, result);
                return result;
            });

        pdfThumbnailCache.set(url, task);
        task.then(applyResult).catch(() => {
            pdfThumbnailCache.delete(url);
        });
    }

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

    function isPdf(filename, mimeType, url) {
        if (mimeType && mimeType.toLowerCase().includes('pdf')) return true;
        if (getExtension(filename) === 'pdf') return true;
        if (typeof url === 'string') {
            const cleanUrl = url.split('?')[0].split('#')[0];
            return /\.pdf$/i.test(cleanUrl);
        }
        return false;
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
        const isPdfFile = isPdf(filename, attachment.mime_type, url);

        const card = document.createElement('div');
        card.className = 'file-preview-card';
        if (isPdfFile) card.classList.add('file-preview-card--pdf');
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
            if (isPdfFile) {
                thumb.classList.add('file-preview-card__thumb--pdf-fallback');
                iconDiv.classList.add('file-preview-card__pdf-mark');
                const lines = document.createElement('div');
                lines.className = 'file-preview-card__pdf-lines';
                lines.innerHTML = '<span></span><span></span><span></span><span></span>';
                thumb.appendChild(lines);
                const ribbon = document.createElement('span');
                ribbon.className = 'file-preview-card__pdf-ribbon';
                ribbon.textContent = 'PDF';
                thumb.appendChild(ribbon);
                const badge = document.createElement('span');
                badge.className = 'file-preview-card__pdf-badge';
                badge.innerHTML = '<i class="fas fa-file-pdf" aria-hidden="true"></i><span>PDF</span>';
                thumb.appendChild(badge);
            }
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

            if (isPdfFile) {
                const kind = document.createElement('div');
                kind.className = 'file-preview-card__kind';
                kind.textContent = 'PDF document';
                info.appendChild(kind);
            }

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

            if (isPdfFile) {
                const action = document.createElement('span');
                action.className = 'file-preview-card__action';
                action.textContent = 'Preview';
                meta.appendChild(action);
            }

            info.appendChild(meta);
            card.appendChild(info);

            if (isPdfFile) {
                renderPdfThumbnail(url, thumb, card, meta);
            }
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
                        <button class="file-preview-modal__btn file-preview-modal__open" title="Open in new tab">
                            <i class="fas fa-arrow-up-right-from-square"></i>
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

        modalElement.querySelector('.file-preview-modal__open').addEventListener('click', () => {
            const url = modalElement.dataset.currentUrl;
            if (url) {
                window.open(url, '_blank', 'noopener');
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
        const pdfPreview = isPdf(filename, mimeType, url);
        const imagePreview = isImage(filename, mimeType) && url;
        const openButton = modal.querySelector('.file-preview-modal__open');
        clearObjectUrl();

        // Store for download
        modal.dataset.currentUrl = url || '';
        modal.dataset.currentFilename = filename;
        modal.dataset.previewKind = pdfPreview ? 'pdf' : (imagePreview ? 'image' : 'file');
        modal.classList.toggle('file-preview-modal--pdf', pdfPreview);
        modal.classList.toggle('file-preview-modal--image', !!imagePreview);
        modal.classList.toggle('file-preview-modal--unsupported', !pdfPreview && !imagePreview && !isTextPreviewable(filename));
        if (openButton) {
            openButton.style.display = url ? '' : 'none';
        }

        // Update header
        titleIcon.className = 'file-preview-modal__title-icon ' + icon;
        titleText.textContent = filename;

        // Update footer
        footerType.textContent = ext.toUpperCase() || 'Unknown';
        footerSize.textContent = formatFileSize(attachment.file_size || attachment.size);

        // Clear previous content
        content.innerHTML = '';

        // Render content based on file type
        if (imagePreview) {
            const img = document.createElement('img');
            img.className = 'file-preview-modal__image';
            img.src = url;
            img.alt = filename;
            content.appendChild(img);
        } else if (pdfPreview && url) {
            const pdfShell = document.createElement('div');
            pdfShell.className = 'file-preview-modal__pdf-shell';

            const pdfPages = document.createElement('div');
            pdfPages.className = 'file-preview-modal__pdf-pages';

            const hint = document.createElement('div');
            hint.className = 'file-preview-modal__pdf-hint';
            const hintText = document.createElement('span');
            hintText.textContent = 'Loading PDF preview...';
            const openLink = document.createElement('a');
            openLink.href = url;
            openLink.target = '_blank';
            openLink.rel = 'noopener';
            openLink.textContent = 'Open in browser';
            hint.appendChild(hintText);
            hint.appendChild(openLink);

            pdfShell.appendChild(pdfPages);
            pdfShell.appendChild(hint);
            content.appendChild(pdfShell);

            fetch(url, { credentials: 'same-origin' })
                .then(response => {
                    if (!response.ok) throw new Error('PDF fetch failed');
                    return response.arrayBuffer();
                })
                .then(async buffer => {
                    if (!window.pdfjsLib || !window.pdfjsLib.getDocument) {
                        throw new Error('PDF.js unavailable');
                    }

                    pdfPages.innerHTML = '';
                    const loadingTask = window.pdfjsLib.getDocument({
                        data: buffer,
                        disableWorker: true
                    });
                    const pdf = await loadingTask.promise;
                    const maxPages = Math.min(pdf.numPages || 0, 20);

                    for (let pageNumber = 1; pageNumber <= maxPages; pageNumber += 1) {
                        const page = await pdf.getPage(pageNumber);
                        const baseViewport = page.getViewport({ scale: 1 });
                        const availableWidth = Math.max(280, Math.min(920, pdfPages.clientWidth - 24));
                        const cssScale = availableWidth / baseViewport.width;
                        const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
                        const viewport = page.getViewport({ scale: cssScale * pixelRatio });
                        const canvas = document.createElement('canvas');
                        const context = canvas.getContext('2d');
                        canvas.className = 'file-preview-modal__pdf-page';
                        canvas.width = Math.floor(viewport.width);
                        canvas.height = Math.floor(viewport.height);
                        canvas.style.width = `${Math.floor(baseViewport.width * cssScale)}px`;
                        canvas.style.height = `${Math.floor(baseViewport.height * cssScale)}px`;
                        pdfPages.appendChild(canvas);
                        await page.render({ canvasContext: context, viewport }).promise;
                    }

                    if (pdf.numPages > maxPages) {
                        const more = document.createElement('div');
                        more.className = 'file-preview-modal__pdf-more';
                        more.textContent = `Showing first ${maxPages} of ${pdf.numPages} pages`;
                        pdfPages.appendChild(more);
                    }

                    hintText.textContent = `${pdf.numPages || maxPages} page${(pdf.numPages || maxPages) === 1 ? '' : 's'}`;
                })
                .catch(() => {
                    pdfPages.innerHTML = '';
                    const iframe = document.createElement('iframe');
                    iframe.className = 'file-preview-modal__pdf';
                    iframe.src = url;
                    iframe.title = filename;
                    iframe.loading = 'lazy';
                    pdfPages.appendChild(iframe);
                    hintText.textContent = 'If the preview does not load, open it in the browser.';
                });
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
                modalElement.classList.remove('file-preview-modal--pdf', 'file-preview-modal--image', 'file-preview-modal--unsupported');
                clearObjectUrl();
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
