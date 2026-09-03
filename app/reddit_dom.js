try {
  if (window.location.hostname.includes('reddit.com')) {
    document.querySelectorAll('div[slot="commentAvatar"], img[alt*="avatar"], reddit-header-large, reddit-sidebar-nav, nav, header').forEach(function(el) { el.remove(); });
    document.querySelectorAll('shreddit-comment').forEach(function(el) {
      var author = el.getAttribute('author') || 'deleted';
      var score = el.getAttribute('score') || '0';
      var depth = parseInt(el.getAttribute('depth') || '0', 10);
      var isOp = el.getAttribute('is-author') === 'true' || el.getAttribute('is_op') === 'true';
      var ts = el.getAttribute('created-timestamp') || el.getAttribute('timestamp') || el.getAttribute('data-timestamp');
      if (!ts) {
        var timeEl = el.querySelector('time');
        if (timeEl) ts = timeEl.getAttribute('datetime') || timeEl.getAttribute('title') || timeEl.textContent.trim();
      }
      if (!ts) {
        var fpt = el.querySelector('faceplate-time-ago');
        if (fpt) ts = fpt.getAttribute('ts') || fpt.getAttribute('timestamp') || fpt.textContent.trim();
      }
      var timeStr = '';
      if (ts) {
        try {
          if (/^\d+$/.test(ts)) {
            var num = parseFloat(ts);
            if (num < 1e11) num *= 1000;
            timeStr = new Date(num).toISOString().replace('T', ' ').substring(0, 16) + ' UTC';
          } else if (ts.includes('T') || ts.includes('-')) {
            var d = new Date(ts);
            if (!isNaN(d.getTime())) {
              timeStr = d.toISOString().replace('T', ' ').substring(0, 16) + ' UTC';
            } else { timeStr = ts; }
          } else { timeStr = ts; }
        } catch(e) {}
      }
      var meta = ['Score: ' + score];
      if (timeStr) meta.push(timeStr);
      var opStr = isOp ? ' [OP]' : '';
      var hTag = 'h' + Math.min(6, 3 + depth);
      var header = document.createElement(hTag);
      header.innerHTML = '<strong>u/' + author + '</strong>' + opStr + ' (' + meta.join(' | ') + ')';
      el.prepend(header);
      el.querySelectorAll('div[slot="creditBar"], div[slot="actions"], div[slot="action-row"], [slot="actionRow"], shreddit-comment-action-row, faceplate-number, shreddit-overflow-menu, [slot="comment-menu"]').forEach(function(x) { x.remove(); });
    });
    document.querySelectorAll('div[slot="creditBar"], shreddit-comment-action-row').forEach(function(el) { el.remove(); });
    document.querySelectorAll('shreddit-post').forEach(function(post) {
      var title = post.getAttribute('post-title') || post.getAttribute('title') || '';
      var author = post.getAttribute('author') || '';
      var score = post.getAttribute('score') || '';
      var postHeader = document.createElement('header');
      if (title) {
        var h1 = document.createElement('h1');
        h1.textContent = title;
        postHeader.appendChild(h1);
      }
      if (author) {
        var metaP = document.createElement('p');
        metaP.innerHTML = '<strong>Posted by u/' + author + '</strong>' + (score ? ' (Score: ' + score + ')' : '');
        postHeader.appendChild(metaP);
      }
      post.prepend(postHeader);
    });
    var isThread = window.location.pathname.includes('/comments/');
    if (isThread) {
      var postEl = document.querySelector('shreddit-post, div[data-testid="post-container"], div[id^="t3_"]');
      var treeEl = document.querySelector('shreddit-comment-tree, #comment-tree, div[slot="comments"], [slot="comments"], shreddit-async-loader[bundlename="comment_tree"]');
      var comments = document.querySelectorAll('shreddit-comment');
      if (postEl && (treeEl || comments.length > 0) && postEl.parentNode) {
        var wrapper = document.createElement('article');
        wrapper.id = 'forage-reddit-thread';
        postEl.parentNode.insertBefore(wrapper, postEl);
        wrapper.appendChild(postEl);
        var heading = document.createElement('h2');
        heading.textContent = 'Comments';
        wrapper.appendChild(heading);
        if (treeEl) {
          wrapper.appendChild(treeEl);
        } else {
          var commentContainer = document.createElement('div');
          comments.forEach(function(c) { commentContainer.appendChild(c); });
          wrapper.appendChild(commentContainer);
        }
      }
    } else {
      var posts = document.querySelectorAll('shreddit-post');
      if (posts.length > 0 && posts[0].parentNode) {
        var feedWrapper = document.createElement('article');
        feedWrapper.id = 'forage-reddit-feed';
        posts[0].parentNode.insertBefore(feedWrapper, posts[0]);
        posts.forEach(function(p) {
          var item = document.createElement('section');
          item.appendChild(p);
          feedWrapper.appendChild(item);
        });
      }
    }
  }
} catch(e) {}
