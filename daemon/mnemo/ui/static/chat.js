/* v3.1 phase 6: ONE chat component, two surfaces (DRY).
 *
 * window.mnemoChat(opts) returns the Alpine data object used by BOTH
 * the full /chat page (surface:'page') and the phase-7 companion dock
 * (surface:'dock'). Parameterised so there is a single implementation.
 *
 *   opts.surface      'page' | 'dock'      (default 'page')
 *   opts.pageContext   {...} auto-attached to dock conversations
 *
 * Streaming is word-smoothed through window.mnemoStreamText (app.js)
 * instead of re-rendering innerHTML per SSE delta, with a real
 * "working" animation while the model is thinking. The thread is a
 * fixed, bottom-pinned viewport that lazy-loads older turns on
 * scroll-up via an IntersectionObserver hitting the paginated
 * /v1/chat/<id>/messages endpoint (model context is bounded
 * separately by server-side compaction).
 */
(function () {
  'use strict';

  function mnemoChat(opts) {
    opts = opts || {};
    var surface = opts.surface || 'page';
    var pageContext = opts.pageContext || null;

    return {
      surface: surface,
      pageContext: pageContext,
      conversations: [],
      activeId: null,
      messages: [],
      citations: [],
      citeSel: '',
      draft: '',
      streaming: false,
      thinking: false,
      working: false, // drives the orbiting-dots "working" animation
      pending: null,
      hasMore: false,
      total: 0,
      tokensTotal: 0,
      provider: 'anthropic',
      bookmarks: [],
      // Rough per-provider context windows -- the budget the running
      // tokens_total is shown against (settings override is v3.x).
      _budgets: {
        anthropic: 200000,
        openai: 128000,
        google: 1000000,
        ollama: 32000,
      },
      _es: null,
      _streamCtl: null,
      _io: null,
      _loadingOlder: false,
      examples: [
        'What do we know about MQTT broker auth?',
        'Trace why the Nebula renderer was reverted.',
        'Draft a memory for what we just learned.',
      ],

      get groupedConversations() {
        var DAY = 86400000;
        var sod = new Date();
        sod.setHours(0, 0, 0, 0);
        var groups = { Today: [], Yesterday: [], Earlier: [] };
        for (var i = 0; i < this.conversations.length; i++) {
          var c = this.conversations[i];
          var t = c.updated_at || 0;
          if (t >= sod.getTime()) groups.Today.push(c);
          else if (t >= sod.getTime() - DAY) groups.Yesterday.push(c);
          else groups.Earlier.push(c);
        }
        return Object.entries(groups)
          .filter(function (e) {
            return e[1].length;
          })
          .map(function (e) {
            return { label: e[0], items: e[1] };
          });
      },

      // --- token budget -------------------------------------------------
      get tokenBudget() {
        return this._budgets[this.provider] || 128000;
      },
      get budgetFrac() {
        return Math.max(0, Math.min(1, this.tokensTotal / this.tokenBudget));
      },
      get budgetWarn() {
        return this.budgetFrac >= 0.85;
      },

      relTime: function (ms) {
        if (!ms) return '';
        var s = (Date.now() - ms) / 1000;
        if (s < 60) return 'now';
        if (s < 3600) return Math.floor(s / 60) + 'm';
        if (s < 86400) return Math.floor(s / 3600) + 'h';
        if (s < 604800) return Math.floor(s / 86400) + 'd';
        return new Date(ms).toLocaleDateString(undefined, {
          month: 'short',
          day: 'numeric',
        });
      },

      mood: function (m) {
        document.dispatchEvent(
          new CustomEvent('mnemo-mnem-mood', { detail: { mood: m } })
        );
      },

      grow: function (el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 180) + 'px';
      },

      // --- lifecycle ----------------------------------------------------
      init: function () {
        var self = this;
        return Promise.resolve(this.loadConversations()).then(function () {
          if (self.conversations.length) {
            self.openConversation(self.conversations[0].id);
          }
          self.$nextTick(function () {
            self._wireLazyHistory();
          });
        });
      },

      loadConversations: function () {
        var self = this;
        return fetch('/v1/chat')
          .then(function (r) {
            return r.json();
          })
          .then(function (list) {
            self.conversations = list;
          });
      },

      newConversation: function () {
        var self = this;
        var body = { name: 'New chat' };
        if (this.pageContext) body.page_context = this.pageContext;
        return fetch('/v1/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (conv) {
            return self.loadConversations().then(function () {
              self.openConversation(conv.id);
              self.$nextTick(function () {
                if (self.$refs.draft) self.$refs.draft.focus();
              });
            });
          });
      },

      openConversation: function (id) {
        var self = this;
        this.activeId = id;
        this.citations = [];
        this.citeSel = '';
        return fetch('/v1/chat/' + id)
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            self.messages = (data.messages || []).map(function (m, i) {
              return Object.assign({}, m, { key: m.id || 'm' + i });
            });
            self.hasMore = !!data.has_more;
            self.total = data.total || self.messages.length;
            self.tokensTotal = data.tokens_total || 0;
            self.provider = data.provider || self.provider;
            self.collectCitations();
            self.loadBookmarks();
            self.scroll(true);
          });
      },

      // --- bookmarks (server-persisted; survives reload + device) ------
      loadBookmarks: function () {
        var self = this;
        if (!this.activeId) return Promise.resolve();
        return fetch('/v1/chat/' + this.activeId + '/bookmarks')
          .then(function (r) {
            return r.ok ? r.json() : [];
          })
          .then(function (bm) {
            self.bookmarks = bm || [];
          })
          .catch(function () {
            self.bookmarks = [];
          });
      },

      isBookmarked: function (seq) {
        return this.bookmarks.some(function (b) {
          return b.message_seq === seq;
        });
      },

      toggleBookmark: function (seq, label) {
        var self = this;
        if (seq == null || !this.activeId) return Promise.resolve();
        var existing = this.bookmarks.find(function (b) {
          return b.message_seq === seq;
        });
        var req = existing
          ? fetch('/v1/chat/' + this.activeId + '/bookmarks/' + existing.id, {
              method: 'DELETE',
            })
          : fetch('/v1/chat/' + this.activeId + '/bookmarks', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ message_seq: seq, label: label || null }),
            });
        return req.then(function () {
          return self.loadBookmarks();
        });
      },

      jumpTo: function (seq) {
        var log = this.$refs.log;
        if (!log) return;
        var el = log.querySelector('[data-seq="' + seq + '"]');
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('jump-flash');
          setTimeout(function () {
            el.classList.remove('jump-flash');
          }, 1200);
        } else if (window.toast) {
          window.toast('Scroll up to load that earlier message', 'info');
        }
      },

      // Lazy scroll-up: prepend the page just before the oldest shown
      // turn, holding the viewport visually still.
      loadOlder: function () {
        var self = this;
        if (this._loadingOlder || !this.hasMore || !this.activeId) return;
        if (!this.messages.length) return;
        this._loadingOlder = true;
        var oldest = this.messages[0].seq;
        var log = this.$refs.log;
        var prevH = log ? log.scrollHeight : 0;
        fetch('/v1/chat/' + this.activeId + '/messages?before=' + oldest + '&limit=30')
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var older = (data.messages || []).map(function (m, i) {
              return Object.assign({}, m, { key: m.id || 'o' + oldest + '_' + i });
            });
            self.messages = older.concat(self.messages);
            self.hasMore = !!data.has_more;
            self.collectCitations();
            self.$nextTick(function () {
              if (log) log.scrollTop = log.scrollHeight - prevH; // anchor
              self._loadingOlder = false;
            });
          })
          .catch(function () {
            self._loadingOlder = false;
          });
      },

      _wireLazyHistory: function () {
        var self = this;
        var sentinel = this.$refs.topSentinel;
        var root = this.$refs.log;
        if (!sentinel || !root || typeof IntersectionObserver === 'undefined') return;
        this._io = new IntersectionObserver(
          function (entries) {
            if (entries[0] && entries[0].isIntersecting) self.loadOlder();
          },
          { root: root, threshold: 0.01 }
        );
        this._io.observe(sentinel);
      },

      collectCitations: function () {
        var seen = [];
        for (var i = 0; i < this.messages.length; i++) {
          var cs = (this.messages[i].content && this.messages[i].content.citations) || [];
          for (var j = 0; j < cs.length; j++) {
            if (seen.indexOf(cs[j]) === -1) seen.push(cs[j]);
          }
        }
        this.citations = seen;
      },

      // [mnemo:<id>] -> a cite link. Run AFTER markdown render (marked
      // keeps the literal brackets; they aren't markdown syntax).
      _citeLinks: function (html) {
        return (html || '').replace(
          /\[mnemo:([^\]\s]+)\]/g,
          '<a href="/node/$1" class="cite-link" onclick="event.stopPropagation()">[mnemo:$1]</a>'
        );
      },

      // The REAL renderer: marked + DOMPurify (window.mnemoMd) so
      // headings / lists / tables / fenced code render like Claude --
      // not the toy fallback below. mnemo-draft fences are stripped
      // (shown as one-click cards instead).
      renderMarkdown: function (t) {
        var noDraft = (t || '').replace(/```mnemo-draft[\s\S]*?```/g, '').trim();
        if (window.mnemoMd) {
          return this._citeLinks(window.mnemoMd(noDraft));
        }
        return this.renderText(t); // marked not hydrated yet -- fallback
      },

      // Fallback only (window.mnemoMd missing on a cold paint).
      renderText: function (t) {
        var esc = function (s) {
          return s.replace(/[&<>]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c];
          });
        };
        var noDraft = (t || '').replace(/```mnemo-draft[\s\S]*?```/g, '').trim();
        return this._citeLinks(
          esc(noDraft)
            .replace(/```([\s\S]*?)```/g, '<pre>$1</pre>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>')
        );
      },

      extractDrafts: function (text) {
        var out = [];
        var re = /```mnemo-draft\s*\n([\s\S]*?)```/g;
        var m;
        while ((m = re.exec(text || '')) !== null) {
          var raw = m[1];
          var fm = raw.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
          var front = {};
          var body = raw.trim();
          if (fm) {
            body = (fm[2] || '').trim();
            var lines = fm[1].split('\n');
            for (var i = 0; i < lines.length; i++) {
              var kv = lines[i].match(/^([A-Za-z_]+):\s*(.*)$/);
              if (kv) front[kv[1]] = kv[2].trim();
            }
          }
          out.push({ raw: raw, front: front, body: body });
        }
        return out;
      },

      saveDraft: function (d) {
        var typeMap = {
          user: 'memory_user',
          feedback: 'memory_feedback',
          project: 'memory_project',
          reference: 'memory_reference',
          session_summary: 'session_summary',
        };
        return fetch('/v1/nodes', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            type: typeMap[d.front.type] || 'memory_feedback',
            name: d.front.name || 'untitled',
            body: d.body,
            project_key: d.front.projectKey || null,
          }),
        }).then(function (r) {
          if (r.ok) {
            return r.json().then(function (n) {
              fetch('/v1/reindex', { method: 'POST' }).catch(function () {});
              if (window.toast) window.toast('Saved memory ' + (n.id || ''), 'success');
            });
          }
          return r.text().then(function (t) {
            if (window.toast) window.toast('Save failed: ' + t, 'error');
          });
        });
      },

      // One-shot markup for a node body. A static preview does NOT
      // need (and the v2.x bucket-stream mnemoRenderBody STALLS after
      // the first fragment -- live-review bug): code -> escaped
      // <pre><code> + Prism; everything else -> marked+DOMPurify.
      previewMarkup: function (box, n) {
        var body = (n && n.body) || '';
        var isCode = window.mnemoIsCodeType && window.mnemoIsCodeType(n.type);
        var ext = window.mnemoLanguageOf
          ? window.mnemoLanguageOf(n.source_path)
          : 'none';
        if (isCode && ext !== 'markdown' && ext !== 'none') {
          var esc = body
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
          box.innerHTML =
            '<pre class="mnemo-code language-' +
            ext +
            '"><code class="language-' +
            ext +
            '">' +
            esc +
            '</code></pre>';
          if (window.Prism) {
            var c = box.querySelector('code');
            try {
              window.Prism.highlightElement(c);
            } catch (e) {
              /* tolerate */
            }
          }
          return;
        }
        box.innerHTML = window.mnemoMd
          ? window.mnemoMd(body)
          : '<pre>' +
            body.replace(/&/g, '&amp;').replace(/</g, '&lt;') +
            '</pre>';
      },

      previewNode: function (cid) {
        var self = this;
        this.citeSel = cid;
        var box = this.$refs.citePreview;
        if (!box) return;
        box.textContent = 'loading…';
        fetch('/v1/nodes/' + cid)
          .then(function (r) {
            return r.ok ? r.json() : null;
          })
          .then(function (n) {
            if (n) {
              self.previewMarkup(box, n);
            } else {
              box.innerHTML = '<a href="/node/' + cid + '">open ' + cid + '</a>';
            }
          })
          .catch(function () {
            box.innerHTML = '<a href="/node/' + cid + '">open ' + cid + '</a>';
          });
      },

      // --- pinned-thread scrolling -------------------------------------
      nearBottom: function () {
        var l = this.$refs.log;
        if (!l) return true;
        return l.scrollHeight - l.scrollTop - l.clientHeight < 120;
      },

      scroll: function (instant) {
        var self = this;
        this.$nextTick(function () {
          var l = self.$refs.log;
          if (l) {
            l.scrollTo({
              top: l.scrollHeight,
              behavior: instant ? 'auto' : 'smooth',
            });
          }
        });
      },

      // Auto-pin to the latest only when the user is already near the
      // bottom (Claude behaviour) -- never yank them up from history.
      pinIfFollowing: function () {
        if (this.nearBottom()) this.scroll(false);
      },

      sendMessage: function () {
        var self = this;
        var text = this.draft.trim();
        if (!text || this.streaming) return Promise.resolve();
        var chain = Promise.resolve();
        if (!this.activeId) {
          chain = this.newConversation().then(function () {
            return new Promise(function (r) {
              setTimeout(r, 50);
            });
          });
        }
        return chain.then(function () {
          var id = self.activeId;
          self.draft = '';
          if (self.$refs.draft) self.$refs.draft.style.height = 'auto';
          self.messages.push({
            key: 'u' + Date.now(),
            role: 'user',
            content: { text: text },
          });
          self.scroll(false);
          return fetch('/v1/chat/' + id + '/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text }),
          }).then(function () {
            self.streamRun(id);
          });
        });
      },

      streamRun: function (id) {
        var self = this;
        this.streaming = true;
        this.thinking = true;
        this.working = true;
        this.mood('thinking');
        var live = this.$refs.liveBody;
        if (live) live.textContent = '';

        // A ReadableStream we push SSE text deltas into; mnemoStreamText
        // paces them out word-by-word (Claude-style cadence).
        var enc = new TextEncoder();
        var controller = null;
        var rs = new ReadableStream({
          start: function (c) {
            controller = c;
          },
        });
        var streamer = null;
        if (window.mnemoStreamText && live) {
          streamer = window.mnemoStreamText(live, rs, {
            unit: 'word',
            perUnitDelayMs: 18,
          });
        }
        this._streamCtl = controller;

        var es = new EventSource('/v1/chat/' + id + '/events');
        this._es = es;
        var finish = function (mood) {
          try {
            if (controller) controller.close();
          } catch (e) {
            /* already closed */
          }
          es.close();
          self._es = null;
          self._streamCtl = null;
          self.streaming = false;
          self.thinking = false;
          self.working = false;
          self.pending = null;
          self.mood(mood || 'idle');
          self.openConversation(id);
        };

        es.addEventListener('thinking', function () {
          self.thinking = true;
          self.working = true;
          self.mood('thinking');
        });
        es.addEventListener('text_delta', function (e) {
          self.thinking = false;
          self.working = false;
          self.mood('speaking');
          var txt = (JSON.parse(e.data).text) || '';
          if (controller) {
            try {
              controller.enqueue(enc.encode(txt));
            } catch (err) {
              /* stream closed */
            }
          } else if (live) {
            live.textContent += txt;
          }
          self.pinIfFollowing();
        });
        es.addEventListener('tool_call', function (e) {
          var d = JSON.parse(e.data);
          self.messages.push({
            key: 'tc' + Date.now(),
            role: 'tool_call',
            content: { name: d.name, args: d.args },
          });
          self.pinIfFollowing();
        });
        es.addEventListener('tool_result', function (e) {
          var d = JSON.parse(e.data);
          self.messages.push({
            key: 'tr' + Date.now(),
            role: 'tool_result',
            content: { id: d.id },
          });
          self.pinIfFollowing();
        });
        es.addEventListener('skill_loaded', function (e) {
          var d = JSON.parse(e.data);
          if (window.toast) window.toast('Mnem loaded skill: ' + d.name, 'info');
        });
        es.addEventListener('permission_request', function (e) {
          self.pending = JSON.parse(e.data);
          self.thinking = false;
          self.working = false;
          self.mood('waiting');
          self.pinIfFollowing();
        });
        es.addEventListener('citation', function (e) {
          var d = JSON.parse(e.data);
          if (self.citations.indexOf(d.node_id) === -1) {
            self.citations.push(d.node_id);
          }
        });
        es.addEventListener('usage', function (e) {
          var d = JSON.parse(e.data);
          if (typeof d.tokens_total === 'number') self.tokensTotal = d.tokens_total;
        });
        es.addEventListener('compaction', function () {
          if (window.toast) window.toast('Mnem compacted the conversation', 'info');
        });
        es.addEventListener('ui_action', function (e) {
          var d = JSON.parse(e.data);
          var a = d.args || {};
          if (d.action === 'navigate' && a.path) {
            setTimeout(function () {
              window.location.href = a.path;
            }, 600);
          } else if (d.action === 'select_node') {
            document.dispatchEvent(
              new CustomEvent('mnemo-select-node', { detail: a })
            );
          } else if (d.action === 'set_filter') {
            document.dispatchEvent(
              new CustomEvent('mnemo-set-filter', { detail: a })
            );
          } else if (d.action === 'scroll_to' && a.selector) {
            var el = document.querySelector(a.selector);
            if (el) el.scrollIntoView({ behavior: 'smooth' });
          } else if (d.action === 'open_panel') {
            document.dispatchEvent(
              new CustomEvent('mnemo-open-panel', { detail: a })
            );
          }
          if (window.toast) window.toast('Mnem: ' + d.action, 'info');
        });
        es.addEventListener('done', function () {
          finish('idle');
        });
        es.addEventListener('idle', function () {
          finish('idle');
        });
        es.addEventListener('cancelled', function () {
          finish('idle');
        });
        es.addEventListener('error', function (e) {
          try {
            var d = JSON.parse(e.data || '{}');
            if (d.message && window.toast) window.toast(d.message, 'error');
          } catch (err) {
            /* bare connection-close */
          }
          finish('alert');
        });
      },

      decide: function (decision) {
        var self = this;
        if (!this.pending) return Promise.resolve();
        var pid = this.pending.id;
        this.pending = null;
        this.thinking = true;
        this.working = true;
        this.mood('thinking');
        return fetch('/v1/chat/' + this.activeId + '/permit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ permission_id: pid, decision: decision }),
        });
      },
    };
  }

  window.mnemoChat = mnemoChat;
})();
