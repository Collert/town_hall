// Share button for the impact card: shares a PNG of the card together with the page link.
// The image is rendered on load so the click can call navigator.share right away
// (browsers only allow it straight after a tap). Where files can't be shared, the
// image is downloaded and the link copied instead.
(function () {
    const card = document.getElementById('impact-card');
    const button = document.getElementById('share-card');
    const { title, filename, copied, saved } = button.dataset;
    const BLANK = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';

    async function render() {
        await document.fonts.ready;
        // Always 1080px wide, whatever the screen size
        // Square corners and no shadow, so apps don't show the transparent edges
        const options = {
            pixelRatio: 1080 / card.offsetWidth,
            imagePlaceholder: BLANK,
            style: { borderRadius: '0', boxShadow: 'none' },
        };
        await htmlToImage.toBlob(card, options);  // Safari only paints fonts and images from the second pass
        const blob = await htmlToImage.toBlob(card, options);
        return new File([blob], filename, { type: 'image/png' });
    }

    const image = window.htmlToImage ? render().catch(() => null) : Promise.resolve(null);

    function copyLink() {
        return navigator.clipboard.writeText(location.href);
    }

    button.addEventListener('click', async () => {
        const file = await image;
        const text = `${title}\n${location.href}`;
        if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
            try {
                await navigator.share({ files: [file], title, text });
            } catch (e) { /* cancelled */ }
            return;
        }
        if (file) {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(file);
            a.download = filename;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
            copyLink().then(() => showToast(saved, 'success'), () => {});
            return;
        }
        if (navigator.share) {
            try {
                await navigator.share({ title, url: location.href });
            } catch (e) { /* cancelled */ }
            return;
        }
        copyLink().then(() => showToast(copied, 'success'));
    });
})();
