/**
 * mnemoProgressive -- four shared client primitives for streaming +
 * staggering UX, plus a single reduced-motion check.
 *
 * Design: docs/plans/2026-05-14-ux-progressive-design.md (§ 1)
 *
 * Every heavy operation in mnemo goes through ONE of these helpers:
 *
 *   - window.mnemoSkeleton(kind, opts)
 *       Returns a DOM node that shimmers in the shape of expected
 *       content. Replace it once data arrives.
 *
 *   - window.mnemoStaggeredReveal(containerEl, items, opts)
 *       Renders ``items`` one-by-one with paced fade-in. Returns
 *       ``{ cancel(), done }``.
 *
 *   - window.mnemoStreamFromSSE(url, opts)
 *       Subscribes to a Server-Sent Events endpoint with
 *       AbortController support. Returns ``{ cancel() }``.
 *
 *   - window.mnemoStreamText(targetEl, source, opts)
 *       Reveals text in a target element char/word/line at a time,
 *       supporting either a string or a ReadableStream as source.
 *       Returns ``{ cancel(), done }``.
 *
 * Accessibility floor: all four consult
 * ``prefers-reduced-motion: reduce`` via a single shared check. When
 * the user prefers reduced motion, all delays collapse to 0 -- content
 * appears in its final state immediately.
 */

(function () {
  'use strict';

  // ----------------------------------------------------------------------
  // Reduced-motion floor. Evaluated once at module init; if the user
  // toggles the OS setting we'd need to re-evaluate, but the cost of a
  // fresh check per call is trivial -- keep it dynamic so the primitives
  // pick up runtime changes too.
  // ----------------------------------------------------------------------
  const prefersReducedMotion = () => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (_) {
      return false;
    }
  };

  // ----------------------------------------------------------------------
  // 1. Skeleton placeholder
  // ----------------------------------------------------------------------
  // Returns a DOM node that the caller can append + later replaceWith()
  // the real content. The placeholder paints a shimmer animation (via
  // CSS class ``.skeleton``) so the user sees "something is coming"
  // within the first frame.
  // ----------------------------------------------------------------------
  window.mnemoSkeleton = function mnemoSkeleton(kind, opts) {
    const o = opts || {};
    const count = Math.max(1, o.count || 3);
    const className = o.className || '';
    const wrapper = document.createElement('div');
    wrapper.className = ('skeleton-block ' + className).trim();
    wrapper.setAttribute('data-skeleton-kind', kind);

    const lineWidthFor = (i, n) => {
      // Vary widths so the placeholder doesn't look like a barcode.
      const widths = ['85%', '95%', '70%', '90%', '60%'];
      return widths[i % widths.length];
    };

    if (kind === 'list' || kind === 'card') {
      for (let i = 0; i < count; i++) {
        const row = document.createElement('div');
        row.className = 'skeleton skeleton-list-row';
        wrapper.appendChild(row);
      }
    } else if (kind === 'code') {
      for (let i = 0; i < count; i++) {
        const line = document.createElement('div');
        line.className = 'skeleton skeleton-code-line';
        line.style.width = lineWidthFor(i, count);
        wrapper.appendChild(line);
      }
    } else if (kind === 'graph') {
      // A single block placeholder for the canvas area.
      const block = document.createElement('div');
      block.className = 'skeleton skeleton-graph';
      wrapper.appendChild(block);
    } else {
      // Default: 'paragraph'
      for (let i = 0; i < count; i++) {
        const line = document.createElement('div');
        line.className = 'skeleton skeleton-paragraph-line';
        line.style.width = lineWidthFor(i, count);
        wrapper.appendChild(line);
      }
    }

    return wrapper;
  };

  // ----------------------------------------------------------------------
  // 2. Staggered reveal
  // ----------------------------------------------------------------------
  // Walks ``items``, calls ``renderOne(item, index)`` to produce each
  // child DOM node, appends to ``containerEl`` with a paced delay
  // between each, and fades each one in via CSS class ``.reveal-item``
  // (which transitions opacity from 0 to 1 over fadeInMs).
  //
  // Returns ``{ cancel(), done }`` where ``done`` is a Promise that
  // resolves when the last item has been appended.
  // ----------------------------------------------------------------------
  window.mnemoStaggeredReveal = function mnemoStaggeredReveal(
    containerEl,
    items,
    opts
  ) {
    const o = opts || {};
    const perItemDelayMs = o.perItemDelayMs == null ? 30 : o.perItemDelayMs;
    const renderOne = o.renderOne;
    if (typeof renderOne !== 'function') {
      throw new TypeError('mnemoStaggeredReveal: opts.renderOne required');
    }
    const reduced = prefersReducedMotion();
    const delay = reduced ? 0 : perItemDelayMs;

    let cancelled = false;
    const timers = [];

    const done = new Promise((resolve) => {
      if (!items || items.length === 0) {
        resolve();
        return;
      }
      for (let i = 0; i < items.length; i++) {
        const t = setTimeout(() => {
          if (cancelled) return;
          const node = renderOne(items[i], i);
          if (!node) {
            if (i === items.length - 1) resolve();
            return;
          }
          if (!reduced) {
            node.classList.add('reveal-item');
          }
          containerEl.appendChild(node);
          // Force a layout tick before adding ``.reveal-in`` so the
          // browser registers the from-state (opacity 0).
          if (!reduced) {
            // Two nested RAFs are the cross-browser way to ensure the
            // opacity:0 starting state paints once before the
            // opacity:1 transition kicks off.
            requestAnimationFrame(() => {
              if (cancelled) return;
              requestAnimationFrame(() => {
                if (cancelled) return;
                node.classList.add('reveal-in');
              });
            });
          }
          if (i === items.length - 1) resolve();
        }, delay * i);
        timers.push(t);
      }
    });

    return {
      cancel() {
        cancelled = true;
        for (const t of timers) clearTimeout(t);
      },
      done,
    };
  };

  // ----------------------------------------------------------------------
  // 3. Server-Sent Events subscription
  // ----------------------------------------------------------------------
  // Wraps ``EventSource`` with:
  //   - per-event-name dispatch (onEvent('name', data))
  //   - AbortSignal support (call signal.abort() to close the stream)
  //   - JSON-decoded payloads (raw text passed through on parse error)
  //   - completion + error callbacks
  //
  // Returns ``{ cancel() }``. The native EventSource handles auto-
  // reconnect on transient failures; ``cancel()`` (or the abort
  // signal) closes it permanently.
  // ----------------------------------------------------------------------
  window.mnemoStreamFromSSE = function mnemoStreamFromSSE(url, opts) {
    const o = opts || {};
    const onEvent = o.onEvent || function () {};
    const onError = o.onError || function () {};
    const onComplete = o.onComplete || function () {};
    const signal = o.signal;
    const knownEvents =
      o.eventNames /* explicit list */ ||
      ['start', 'file', 'event', 'message', 'done', 'busy', 'error'];

    const source = new EventSource(url);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      try {
        source.close();
      } catch (_) {
        /* already closed */
      }
    };

    const handleData = (name) => (msg) => {
      let payload = msg.data;
      try {
        payload = JSON.parse(msg.data);
      } catch (_) {
        /* not JSON; pass through */
      }
      onEvent(name, payload);
      if (name === 'done') {
        close();
        onComplete();
      }
    };

    // Generic "message" events (no named event:) always come through
    // source.onmessage; named events come through addEventListener.
    source.onmessage = handleData('message');
    for (const name of knownEvents) {
      if (name === 'message') continue;
      source.addEventListener(name, handleData(name));
    }
    source.onerror = (err) => {
      // EventSource calls onerror for transient connection drops too,
      // when readyState becomes CONNECTING. We only propagate when the
      // connection is definitively CLOSED.
      if (source.readyState === EventSource.CLOSED) {
        close();
        onError(err);
      }
    };

    if (signal) {
      if (signal.aborted) {
        close();
      } else {
        signal.addEventListener('abort', close, { once: true });
      }
    }

    return {
      cancel: close,
    };
  };

  // ----------------------------------------------------------------------
  // 4. Streaming text reveal
  // ----------------------------------------------------------------------
  // ``source`` can be a string (paced reveal only) or a ReadableStream
  // of UTF-8 text (paced reveal of chunks AS they arrive). Either way
  // the call sites use the same API.
  //
  // ``unit`` selects the reveal granularity: 'char' | 'word' | 'line'.
  //   - 'char': fastest, looks like a typing effect
  //   - 'word': default, paces nicely for prose
  //   - 'line': cheaper for code (one Prism re-highlight per N lines)
  //
  // Returns ``{ cancel(), done }`` where ``cancel()`` flushes the
  // remaining content immediately (no half-rendered state).
  // ----------------------------------------------------------------------
  window.mnemoStreamText = function mnemoStreamText(targetEl, source, opts) {
    const o = opts || {};
    const unit = o.unit || 'word';
    const perUnitDelayMs = o.perUnitDelayMs == null ? 12 : o.perUnitDelayMs;
    const formatLine = o.formatLine || ((s) => s);
    const reduced = prefersReducedMotion();
    const delay = reduced ? 0 : perUnitDelayMs;

    // Tokenize a chunk of text into reveal units.
    const tokenize = (text) => {
      if (unit === 'char') return text.split('');
      if (unit === 'line') return text.split(/(\n)/); // preserve newlines
      // word: split on whitespace, keeping the whitespace
      return text.split(/(\s+)/);
    };

    let cancelled = false;
    targetEl.textContent = '';
    const buffer = [];
    let flushIdx = 0;
    let flushTimer = null;

    const flushOne = () => {
      if (cancelled) return;
      if (flushIdx >= buffer.length) {
        flushTimer = null;
        return;
      }
      const tok = buffer[flushIdx++];
      if (unit === 'line') {
        if (tok === '\n') {
          targetEl.appendChild(document.createTextNode('\n'));
        } else if (tok !== '') {
          const formatted = formatLine(tok);
          targetEl.appendChild(document.createTextNode(formatted));
        }
      } else {
        targetEl.appendChild(document.createTextNode(tok));
      }
      flushTimer = setTimeout(flushOne, delay);
    };

    const enqueueText = (text) => {
      buffer.push(...tokenize(text));
      if (flushTimer == null) flushOne();
    };

    let done;
    if (typeof source === 'string') {
      // Whole string available -- pace it out.
      if (reduced) {
        targetEl.textContent = source;
        done = Promise.resolve();
      } else {
        enqueueText(source);
        done = new Promise((resolve) => {
          const tick = () => {
            if (cancelled || flushIdx >= buffer.length) resolve();
            else setTimeout(tick, delay * 2);
          };
          tick();
        });
      }
    } else if (source && typeof source.getReader === 'function') {
      // ReadableStream: feed chunks into the buffer as they arrive.
      const reader = source.getReader();
      const decoder = new TextDecoder();
      done = (async () => {
        for (;;) {
          if (cancelled) break;
          const { done: end, value } = await reader.read();
          if (end) break;
          enqueueText(decoder.decode(value, { stream: true }));
        }
        enqueueText(decoder.decode()); // flush
      })();
    } else {
      // Empty / unknown source -- finish silently.
      done = Promise.resolve();
    }

    return {
      cancel() {
        cancelled = true;
        if (flushTimer) clearTimeout(flushTimer);
        // Render any remaining buffered tokens in one shot so the
        // caller never sees a half-finished string.
        while (flushIdx < buffer.length) {
          const tok = buffer[flushIdx++];
          if (unit === 'line' && tok === '\n') {
            targetEl.appendChild(document.createTextNode('\n'));
          } else if (tok != null && tok !== '') {
            const out = unit === 'line' ? formatLine(tok) : tok;
            targetEl.appendChild(document.createTextNode(out));
          }
        }
      },
      done,
    };
  };

  // Expose the reduced-motion probe so call sites can branch their own
  // logic on it too (e.g. cy.animate durations).
  window.mnemoPrefersReducedMotion = prefersReducedMotion;
})();
