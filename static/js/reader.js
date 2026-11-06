// HTMX handles navigation (including arrow keys via hx-trigger). This shim
// only covers touch swipes and next-image prefetch, which HTMX does not.
(function () {
  function go(direction) {
    const link = document.querySelector('#reader [data-nav="' + direction + '"]');
    if (link) link.click();
  }
  let start = null;
  document.addEventListener('touchstart', function (event) {
    start = { x: event.touches[0].clientX, y: event.touches[0].clientY };
  }, { passive: true });
  document.addEventListener('touchend', function (event) {
    if (start === null) return;
    const dx = event.changedTouches[0].clientX - start.x;
    const dy = event.changedTouches[0].clientY - start.y;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      go(dx < 0 ? 'next' : 'prev');
    }
    start = null;
  }, { passive: true });
  function preload() {
    const reader = document.getElementById('reader');
    if (!reader || !reader.dataset.nextImage) return;
    const image = new Image();
    image.src = reader.dataset.nextImage;
  }
  document.addEventListener('DOMContentLoaded', preload);
  document.addEventListener('htmx:afterSwap', preload);
})();
