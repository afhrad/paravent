/* When the gamemaster moves the table to another world while a page is open, say so and
   offer to reload (documentation/CURRENT-WORLD.md §8). The page knows the world it was
   drawn in; the stream names the table's world now, first on connecting, then on every
   change, as "<id> <name>". */
(function () {
  var notice = document.getElementById('world-notice');
  if (!notice || !window.EventSource) return;
  var drawn = notice.dataset.world || '';
  var text = notice.querySelector('.world-notice-text');

  function show(id, name) {
    if (id === drawn) { notice.hidden = true; return; }  // back where the page was drawn
    text.textContent = id ? notice.dataset.text.replace('{world}', name) : notice.dataset.textNone;
    notice.hidden = false;
  }

  var source = new EventSource('/table/events');
  source.addEventListener('revision', function (e) {
    var data = e.data || '';
    var cut = data.indexOf(' ');
    show(cut < 0 ? data : data.slice(0, cut), cut < 0 ? '' : data.slice(cut + 1));
  });
  notice.addEventListener('click', function (e) {
    if (e.target.closest('[data-world-reload]')) window.location.reload();
    else if (e.target.closest('[data-world-dismiss]')) notice.hidden = true;
  });
})();
