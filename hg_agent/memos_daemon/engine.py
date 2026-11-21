import re, json
from pathlib import Path
from .logging_setup import log
from .prompt import (load_prompt, PROMPT_SCHEMA_VERSION, ASK_SYSTEM_PROMPT,
                     CHAT_SYSTEM_PROMPT, WRITE_SYSTEM_PROMPT, STORY_SYSTEM_PROMPT)


def _cfg_bool(v, default=True):
    """Coerce a config value to bool, tolerating JSON-file strings.

    Hand-edited config files often carry "true"/"false" as STRINGS. Plain
    truthiness gets that dangerously wrong: bool("false") is True, so a user who
    writes "false" to disable something would silently enable it. Parse the
    common textual forms explicitly instead.
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "on", "1"):
            return True
        if s in ("false", "no", "off", "0", ""):
            return False
    return default


_SYS_PROMPT_TOK_CACHE = {}

def _chars_per_token(text: str) -> float:
    """Estimate chars-per-token from the text's own character mix.

    Tokenizers merge long runs of ordinary letters into single tokens, so English
    prose averages ~4 chars/token. Digits, punctuation and symbols mostly do NOT
    merge, so log lines, timestamps, IDs, code and tables run far denser (~2-2.5).
    Treating everything as prose under-counts dense text badly — and under-
    counting is the direction that gets input REJECTED by the model.

    We measure the fraction of non-alphabetic, non-space characters and
    interpolate between a dense floor and a prose ceiling, rounding conservative.
    """
    if not text:
        return 4.0
    sample = text[:20000]                    # enough to characterise; bounded cost
    dense = sum(1 for c in sample if not (c.isalpha() or c.isspace()))
    frac = dense / len(sample)
    # frac 0.0 (pure prose) → 4.0 ; frac 0.30+ (logs/code/data) → 2.2
    ratio = 4.0 - (frac / 0.30) * 1.8
    return max(2.2, min(4.0, ratio))

def _sys_prompt_tokens(cfg) -> int:
    """Approximate token cost of the system prompt, measured not guessed.

    It's prefilled on every enrichment and is the largest fixed input cost, so
    budget maths that ignores it under-counts badly. Cached per prompt source —
    the prompt only changes when the operator swaps prompt_file.
    """
    try:
        sp = load_prompt(cfg) or ""
    except Exception:
        return 2400                      # conservative fallback
    key = len(sp)
    if key not in _SYS_PROMPT_TOK_CACHE:
        _SYS_PROMPT_TOK_CACHE[key] = max(1, key // 4)   # ~4 chars/token
    return _SYS_PROMPT_TOK_CACHE[key]

try:
    import litert_lm
    LITERT_AVAILABLE = True
except ImportError:
    LITERT_AVAILABLE = False

_SCHEMA_WORDS = {"task", "text", "category", "work", "personal", "blocked_by", ""}

class GemmaEngine:
    def __init__(self, model_path, cfg: dict | None = None):
        self._cfg = cfg or {}
        if not LITERT_AVAILABLE:
            raise RuntimeError("litert_lm not installed: pip install litert-lm --break-system-packages")
        if not model_path or not Path(model_path).exists():
            raise FileNotFoundError(f"Model not found: {model_path!r}")
        log.info(f"Loading model from {model_path} ...")
        try:
            litert_lm.set_min_log_severity(litert_lm.LogSeverity.ERROR)
        except Exception:
            pass
        try:
            backend = litert_lm.Backend.CPU()
        except Exception:
            backend = litert_lm.Backend.CPU
        # Raise the model's token budget. This litert_lm build accepts
        # max_num_tokens at Engine construction (total context) and, per-session,
        # max_output_tokens on create_session. We set both: a generous context
        # here, and the per-generation output cap in _generate() via a Session.
        self._max_tokens = int(self._cfg.get("enrich_max_tokens", 2048))
        self._max_context = int(self._cfg.get("model_max_num_tokens", 4096))
        # Optional: also load the vision / audio towers so the model itself can
        # read photos and transcribe speech (media_backend = "litert"). Only
        # attempted when asked for, and only with kwargs this build accepts —
        # otherwise the engine is built exactly as before.
        extra = {}
        self._mm_enabled = False
        if self._cfg.get("media_litert_multimodal", False):
            try:
                import inspect
                params = inspect.signature(litert_lm.Engine).parameters
                for k in ("vision_backend", "audio_backend"):
                    if k in params:
                        extra[k] = backend
                self._mm_enabled = bool(extra)
                if not extra:
                    log.warning("media_litert_multimodal is on, but this litert_lm build "
                                "has no vision/audio backend options — media stays text-only")
            except Exception as e:
                log.warning(f"couldn't probe multimodal support: {e}")
        try:
            self._engine = litert_lm.Engine(model_path, backend=backend,
                                            max_num_tokens=self._max_context, **extra)
        except TypeError:
            # Older/newer builds without max_num_tokens — construct plainly.
            self._mm_enabled = False
            self._engine = litert_lm.Engine(model_path, backend=backend)
        self._engine.__enter__()
        # Max output tokens. litert_lm truncates generation at a built-in default
        # that's too low for enriching long notes (the JSON gets cut off mid-
        # response). Raise it. The binding's API for this has varied across
        # versions, so we probe once at startup for the mechanism that exists and
        # remember it — see _generate(). Configurable via 'enrich_max_tokens'.
        self._max_tokens = int(self._cfg.get("enrich_max_tokens", 2048))
        # Generation path. The plain create_conversation path is PROVEN on this
        # device: it completes full enrichments (polish included). The low-level
        # Session path (create_session → run_prefill → run_decode) enforces an
        # exact output cap, but run_decode() streams in chunks and must be
        # looped — a single call returns a partial response that looks exactly
        # like truncation. That loop is now implemented in _generate(), but the
        # path stays OPT-IN until verified on-device, because enabling it
        # unverified silently broke every enrichment (polish lost, only
        # title/tags salvaged) while looking superficially fine in the logs.
        # Speed comes from model_max_num_tokens (context size), NOT from capping
        # output — so there is no speed reason to prefer the session path.
        if self._cfg.get("use_session_generation", False):
            self._gen_mechanism = self._detect_token_mechanism()
        else:
            self._gen_mechanism = None
        log.info(f"Model loaded. (max_output_tokens={self._max_tokens}, "
                 f"mechanism={self._gen_mechanism or 'default/unavailable'})")

    def _detect_token_mechanism(self):
        """Figure out how this litert_lm build accepts a max-output-tokens value.
        Returns a tag used by _generate(); None if we couldn't find a knob (we
        then fall back to the library default, and the salvage parser covers any
        truncation)."""
        import inspect
        _TOK_NAMES = ("max_output_tokens", "max_tokens", "max_decode_tokens",
                      "max_new_tokens", "output_tokens", "max_length", "max_decode_steps")
        # 0) PREFERRED: create_session(max_output_tokens=...) — a dedicated output
        #    cap (this is what current litert_lm builds expose). We generate via a
        #    Session in _generate() when this is present.
        try:
            cs = inspect.signature(litert_lm.Engine.create_session).parameters
            for pname in _TOK_NAMES:
                if pname in cs:
                    self._gen_sess_param = pname
                    return f"create_session.{pname}"
        except Exception:
            pass
        for cfg_name in ("GenerationConfig", "SamplerConfig", "DecodeConfig",
                         "GenerateConfig", "InferenceConfig", "SessionConfig"):
            cls = getattr(litert_lm, cfg_name, None)
            if cls is None:
                continue
            try:
                params = inspect.signature(cls.__init__).parameters
                for pname in _TOK_NAMES:
                    if pname in params:
                        self._gen_cfg_cls = cls
                        self._gen_cfg_param = pname
                        return f"config:{cfg_name}.{pname}"
            except Exception:
                pass
        # 2) A kwarg accepted directly by create_conversation.
        try:
            cc = inspect.signature(litert_lm.Engine.create_conversation).parameters
            for pname in _TOK_NAMES:
                if pname in cc:
                    self._gen_conv_param = pname
                    return f"create_conversation.{pname}"
        except Exception:
            pass
        # 3) A kwarg accepted by send_message itself.
        try:
            for n in dir(litert_lm):
                obj = getattr(litert_lm, n)
                if inspect.isclass(obj) and hasattr(obj, "send_message"):
                    sm = inspect.signature(obj.send_message).parameters
                    for pname in _TOK_NAMES:
                        if pname in sm:
                            self._gen_send_param = pname
                            return f"send_message.{pname}"
        except Exception:
            pass
        return None

    def _generate(self, messages, prompt, cap=None):
        """Single place that runs generation, applying the max-token limit via
        whatever mechanism this litert_lm build supports (detected at startup).
        Falls back to a plain call if none was found."""
        mech = self._gen_mechanism
        # Per-call output cap: a short note needs a short generation. Falls back
        # to the model-load default when a caller doesn't specify one.
        _cap = int(cap) if cap else self._max_tokens
        try:
            if mech and mech.startswith("create_session."):
                # This litert_lm build generates via a low-level Session:
                #   create_session(max_output_tokens=N) → run_prefill([text])
                #   → run_decode() → Responses. This is the path that actually
                #   enforces the output-token cap.
                # The session has no system-message slot, so fold the system
                # prompt (the enrichment schema) into the prefill text.
                sys_text = ""
                for m in (messages or []):
                    if m.get("role") == "system":
                        c = m.get("content")
                        if isinstance(c, list) and c and isinstance(c[0], dict):
                            sys_text = c[0].get("text", "") or ""
                        elif isinstance(c, str):
                            sys_text = c
                        break
                full = (sys_text + "\n\n" + prompt) if sys_text else prompt
                kw = {self._gen_sess_param: _cap}
                if getattr(self, "_sampler_cfg", None) is not None:
                    kw["sampler_config"] = self._sampler_cfg
                sess = self._engine.create_session(**kw)
                try:
                    sess.run_prefill([full])
                    # run_decode() yields the NEXT chunk, not the whole response.
                    # Calling it once returns a partial generation that looks
                    # exactly like a truncated answer (and identically so at any
                    # max_output_tokens, since the cap is never what stops it).
                    # Loop until the model stops producing new text, bounded so a
                    # misbehaving build can't spin forever.
                    parts, guard = [], 0
                    while guard < 4096:
                        guard += 1
                        chunk = self._responses_text(sess.run_decode())
                        if not chunk:
                            break
                        parts.append(chunk)
                        # Some builds return the full text-so-far rather than a
                        # delta; detect that and keep only the latest.
                        if len(parts) > 1 and parts[-1].startswith(parts[-2]):
                            parts = [parts[-1]]
                    return "".join(parts) if len(parts) != 1 else parts[0]
                finally:
                    try: sess.close()
                    except Exception: pass
            if mech and mech.startswith("config:"):
                cfg = self._gen_cfg_cls(**{self._gen_cfg_param: _cap})
                # Try passing the config to create_conversation, then to send_message.
                try:
                    with self._engine.create_conversation(messages=messages, config=cfg) as conv:
                        raw = conv.send_message(prompt)
                        return raw.get("content", [{}])[0].get("text", "") or ""
                except TypeError:
                    with self._engine.create_conversation(messages=messages) as conv:
                        raw = conv.send_message(prompt, config=cfg)
                        return raw.get("content", [{}])[0].get("text", "") or ""
            if mech and mech.startswith("create_conversation."):
                kw = {self._gen_conv_param: _cap}
                with self._engine.create_conversation(messages=messages, **kw) as conv:
                    raw = conv.send_message(prompt)
                    return raw.get("content", [{}])[0].get("text", "") or ""
            if mech and mech.startswith("send_message."):
                kw = {self._gen_send_param: _cap}
                with self._engine.create_conversation(messages=messages) as conv:
                    raw = conv.send_message(prompt, **kw)
                    return raw.get("content", [{}])[0].get("text", "") or ""
        except Exception as e:
            log.warning(f"max-token mechanism '{mech}' failed ({e}); using default")
        # Fallback: plain call (library default token limit; salvage parser covers truncation).
        with self._engine.create_conversation(messages=messages) as conv:
            raw = conv.send_message(prompt)
            return raw.get("content", [{}])[0].get("text", "") or ""

    @staticmethod
    def _responses_text(resp) -> str:
        """Extract generated text from a litert_lm Responses object. The exact
        shape varies, so we try the likely forms in order and fall back to str().
        """
        if resp is None:
            return ""
        if isinstance(resp, str):
            return resp
        # Common attribute names holding the text.
        for attr in ("text", "content", "output_text", "response", "message"):
            v = getattr(resp, attr, None)
            if isinstance(v, str) and v:
                return v
            # nested {"content":[{"text":...}]}-style
            if isinstance(v, (list, tuple)) and v:
                first = v[0]
                if isinstance(first, dict) and first.get("text"):
                    return first["text"]
                if isinstance(first, str):
                    return "".join(x for x in v if isinstance(x, str))
        # Mapping form.
        if isinstance(resp, dict):
            c = resp.get("content")
            if isinstance(c, list) and c and isinstance(c[0], dict):
                return c[0].get("text", "") or ""
            for k in ("text", "output_text", "response"):
                if isinstance(resp.get(k), str):
                    return resp[k]
        # Iterable of response chunks/strings.
        try:
            parts = []
            for item in resp:
                if isinstance(item, str):
                    parts.append(item)
                else:
                    t = getattr(item, "text", None)
                    if isinstance(t, str):
                        parts.append(t)
            if parts:
                return "".join(parts)
        except TypeError:
            pass
        return str(resp)

    @staticmethod
    def _is_token_overflow(e) -> bool:
        """True if an inference exception may be the model rejecting oversized input.

        litert_lm CAN raise with a clear message ('Input token ids are too long.
        Exceeding the maximum number of tokens allowed: 4292 >= 4096'), but on
        this build the detail is printed by the native layer to stderr and the
        Python exception is only 'litert_lm_conversation_send_message failed'.
        Matching just the clear wording meant the trim-and-retry backstop NEVER
        fired for the case it exists for, and the note failed repeatedly instead.

        So we also treat a bare send_message/inference failure as a possible
        overflow. A false positive costs one extra trimmed retry, which is
        harmless; a false negative costs the enrichment entirely.
        """
        msg = str(e).lower()
        if "token" in msg and ("too long" in msg or "exceeding" in msg
                               or "maximum number of tokens" in msg):
            return True
        # Generic native failure — the detail may only exist in stderr.
        return ("send_message" in msg or "conversation" in msg
                or "invalid_argument" in msg)

    def _hard_trim(self, system_prompt: str, prompt: str) -> str:
        """Aggressively shrink the note after the model rejected it as too long.

        Halves the room the estimate thought was available and trims to that.
        Used only on the retry path — the model has already told us the estimate
        was wrong, so we stop estimating and just make it decisively smaller.
        """
        total = int(self._cfg.get("model_max_num_tokens", 4096))
        # Very conservative: assume the whole context minus a generous response
        # margin, at a pessimistic 2.5 chars/token, then HALVE the note's share.
        safe_input_chars = int((total - 800) * 2.5)
        note_budget = max(300, (safe_input_chars - len(system_prompt)) // 2)
        if len(prompt) <= note_budget:
            # Estimate said it fits but the model disagreed — force a hard cap.
            note_budget = min(note_budget, max(300, len(prompt) // 2))
        marker = "\n\n[… note heavily truncated to fit the model …]\n\n"
        keep = max(300, note_budget - len(marker))
        head = int(keep * 0.6)
        tail = keep - head
        return prompt[:head] + marker + prompt[-tail:]

    def _fit_input(self, system_prompt: str, prompt: str, produces_polish: bool = True) -> str:
        """Trim the note portion only if system_prompt + note approaches the
        model's total context window.

        The constraint the model actually enforces is INPUT tokens < total
        context (the error was "Input token ids too long: 4473 >= 4096"). Output
        grows into the KV cache during generation; it does NOT subtract its
        maximum from the input ceiling. An earlier version reserved the full
        enrich_max_tokens (2048) up front, which — with a ~2400-token system
        prompt — left negative room and trimmed EVERY note to a 200-char floor,
        even tiny ones. That was wrong.

        Correct model: keep a modest output headroom so the response has somewhere
        to go, and only trim when the input genuinely crowds the window. For a
        typical note this is a no-op; only a very large note (the 4473-token case)
        gets trimmed.

        Estimate is conservative (~3.5 chars/token) and rounds the char budget
        DOWN, since under-estimating tokens is the dangerous direction.
        """
        total = int(self._cfg.get("model_max_num_tokens", 4096))
        # Headroom the response needs within the same context. This is HEADROOM,
        # not a pre-reservation of max output — the model streams into whatever's
        # left. Keep it modest so normal notes never trip the guard.
        if produces_polish:
            out_headroom = int(self._cfg.get("enrich_input_headroom_polish", 400))
        else:
            out_headroom = int(self._cfg.get("enrich_input_headroom_meta", 250))
        # Chars per token. English PROSE is ~4, but this system prompt is dense
        # markdown+JSON (field names, braces, punctuation) which tokenises to
        # ~3.0 chars/token — a note that "fit" a single 4-char estimate still
        # overflowed at 4292 tokens in the field. Estimating the prompt and the
        # note SEPARATELY (prompt dense at 3.0, note prose at 4.0) fixes both the
        # under-count that let overflow through AND the over-count that trimmed
        # small notes. The retry in enrich() is the hard backstop if even this
        # per-part estimate is wrong.
        budget_tokens = max(512, total - out_headroom - 64)
        sys_tokens = len(system_prompt) / 3.0        # dense → fewer chars/token
        note_token_room = budget_tokens - sys_tokens

        # Chars-per-token for the NOTE, measured from its own content rather than
        # assumed. Plain English prose runs ~4 chars/token, but pasted logs,
        # timestamps, code, IDs and tables are far denser (~2-2.5) because digits
        # and punctuation rarely merge into big tokens. Assuming prose for a log
        # dump under-counts by ~40% — which is exactly how a note sailed past this
        # guard and got rejected by the model at 6358 >= 6144. Under-estimating is
        # the dangerous direction, so we measure density and round DOWN.
        ratio = _chars_per_token(prompt)
        note_tokens = len(prompt) / ratio
        if note_tokens <= note_token_room:
            return prompt                            # fits — common case, no-op

        # Only here for a genuinely large note. Trim the NOTE (never the system
        # prompt) to its remaining token room, back to chars at the SAME measured
        # ratio used to decide it didn't fit.
        note_budget = int(max(150, note_token_room) * ratio)
        if len(prompt) <= note_budget:
            return prompt
        marker = "\n\n[… note truncated to fit the model's context window …]\n\n"
        keep = note_budget - len(marker)
        if keep < 400:
            trimmed = prompt[:note_budget]
        else:
            head = int(keep * 0.6)
            tail = keep - head
            trimmed = prompt[:head] + marker + prompt[-tail:]
        from .logging_setup import log
        log.warning(f"    large note (~{len(prompt)//4} tok) trimmed to fit the "
                    f"{total}-tok context; title/tags/summary still produced")
        return trimmed

    def enrich(self, note_text, hints: dict | None = None) -> dict | None:
        """Run inference. Returns the parsed enrichment dict with an added
        '_latency_s' key, or None on failure.

        hints: optional soft context for reprocessing — e.g. the prior title and
        tags from a previously approved note. They steer consistency but must not
        override what the current note text actually says."""
        import time
        body = note_text.strip()
        # Quick-capture heuristic: very short or list-shaped notes stay verbatim
        # (a shopping list or "call mum" shouldn't be rewritten). The character
        # threshold is configurable — see enrich_quick_note_chars in config.py.
        # Lower it so short PROSE (which may have typos worth fixing) still gets
        # polished; raise it to leave more short notes untouched. Set 0 to treat
        # notes as "quick" only when they're list-shaped, never by length alone.
        lines = [l for l in body.split("\n") if l.strip()]
        list_like = lines and sum(
            1 for l in lines if l.lstrip()[:2] in ("- ", "* ")
            or l.lstrip()[:1].isdigit()
            or l.lstrip().startswith("[ ]") or l.lstrip().startswith("[]")
        ) >= max(2, len(lines) * 0.6)
        quick_chars = int(self._cfg.get("enrich_quick_note_chars", 120))
        quick = (quick_chars > 0 and len(body) <= quick_chars) or list_like
        # On-demand Polish: when the user explicitly asks for a rewrite, honour
        # it regardless of length or list-shape — it overrides the skip rules
        # below. Stateless: the request rides in via hints for this run only.
        force_polish = bool(hints and hints.get("force_polish"))
        # Long-note handling (fully configurable — see config.py):
        #   enrich_skip_polish_enabled : master on/off switch for this behavior.
        #   enrich_skip_polish_words   : word count at/above which it applies.
        #   enrich_long_summary_sentences : cap on summary length for long notes.
        # When enabled, long notes skip the AI "polished" rewrite (the user's
        # long-form text is authoritative) and get a bounded summary, keeping the
        # response compact. Turn off (or raise the threshold high) to always
        # attempt a full polished rewrite regardless of length.
        skip_polish_on = _cfg_bool(self._cfg.get("enrich_skip_polish_enabled", True))
        big_words = int(self._cfg.get("enrich_skip_polish_words", 400))
        # SELF-CORRECTING: a polished note must fit  system-prompt + note + output
        # inside the context, or we'd trim the note and then "polish" text the
        # model only partly saw — silently rewriting a truncated note. The user's
        # config file is a sparse override and can outlive a context change (e.g.
        # a 1536 threshold left over from a 32K context), so we clamp to what
        # ACTUALLY fits rather than trusting the number. Logged once when it bites
        # so the mismatch is visible instead of silent.
        _ctx = int(self._cfg.get("model_max_num_tokens", 4096))
        _out = int(self._cfg.get("enrich_max_tokens", 2048))
        _fits_words = max(60, int((_ctx - _sys_prompt_tokens(self._cfg) - _out) / 1.4))
        if big_words > _fits_words:
            if not getattr(self, "_warned_polish_clamp", False):
                log.warning(
                    f"    enrich_skip_polish_words={big_words} exceeds what the "
                    f"{_ctx}-token context fits (~{_fits_words} words); clamping. "
                    f"Raise model_max_num_tokens to polish longer notes.")
                self._warned_polish_clamp = True
            big_words = _fits_words
        summary_cap = int(self._cfg.get("enrich_long_summary_sentences", 2))
        word_count = len(body.split())
        large = skip_polish_on and big_words > 0 and word_count >= big_words
        # Metadata-only when: the global default is metadata-only, OR this
        # specific note asked for it (the "protected" hint, set for meta_only /
        # protected / user-edited notes). Either way, keep the body verbatim.
        # A force_polish request overrides both.
        hint_meta = bool(hints and hints.get("protected"))
        metadata_only = (not force_polish
                         and (hint_meta
                              or not self._cfg.get("enrich_polish_by_default", False)))
        prompt = f"Note to analyze:\n\n{body}"
        if force_polish:
            prompt += (
                "\n\nThe user explicitly requested a polished rewrite. Provide a "
                "\"polished\" version that improves clarity and flow while "
                "PRESERVING the user's meaning, names, and quotes exactly. Also "
                "provide a title and relevant tags."
            )
        elif metadata_only:
            prompt += (
                "\n\nSet \"polished\" to an empty string — do NOT rewrite the "
                "note; the user's text stands exactly as written. Provide only a "
                "concise title, a short summary, relevant tags, and any action "
                "items or reminders you find."
            )
        elif quick:
            prompt += (
                "\n\nThis is a short quick-capture note or a list. Set "
                "\"polished\" to an empty string — keep the user's text exactly "
                "as-is. Only provide a short title and relevant tags."
            )
        elif large:
            prompt += (
                "\n\nThis is a long note. Set \"polished\" to an empty string "
                "(do NOT rewrite it — the user's text stands as-is). Keep "
                f"\"summary\" to at most {summary_cap} sentence"
                f"{'s' if summary_cap != 1 else ''}. Provide a concise title and "
                "relevant tags. Keep the whole response compact."
            )
        if hints:
            prior_title = (hints.get("prior_title") or "").strip()
            prior_tags = hints.get("prior_tags") or []
            hint_lines = []
            if prior_title:
                hint_lines.append(f"- previous title: {prior_title}")
            if prior_tags:
                hint_lines.append(f"- previous tags: {', '.join(prior_tags)}")
            if hints.get("protected"):
                hint_lines.append(
                    "- the user wants the note text preserved VERBATIM: set "
                    "\"polished\" to an empty string; only provide title, summary, "
                    "tags and action_items")
            vocab = hints.get("vocab_tags") or []
            if hint_lines:
                prompt += (
                    "\n\nContext (soft hints only — the note text above is the "
                    "source of truth):\n" + "\n".join(hint_lines)
                )
            # Tag vocabulary is NOT a soft hint — tag consistency across notes is
            # what makes them findable and connectable, so state it as a rule.
            # (Framed as a "soft hint" the model ignored it and invented a fresh
            # tag per note, leaving related notes with nothing in common.)
            if vocab:
                prompt += (
                    "\n\nTAG VOCABULARY — tags already used in this notebook:\n"
                    + ", ".join(vocab)
                    + "\nHOW TO USE IT: reuse a tag from this list when it "
                      "GENUINELY describes this note — consistent tags are what "
                      "link related notes together. But a WRONG tag is worse than "
                      "a new one: it buries this note among unrelated ones and "
                      "makes filtering useless. If nothing above fits, create a "
                      "new specific tag. If only one tag fits, return only one — "
                      "never pad the list to reach a count.\n"
                      "The list is ordered by how often each tag is used, so the "
                      "first entries are simply your most common subjects. Do NOT "
                      "reach for a frequent tag because it is frequent: a personal "
                      "errand is not work, and a shopping list is not a project."
                )
        system_prompt = load_prompt(self._cfg)
        # A note produces a full polished rewrite unless it's long/quick (which
        # skip it) — UNLESS the user explicitly forced polish. That determines
        # how much output budget to reserve, which in turn sets how much of the
        # note can fit as input. Large notes skip polish, so they get the large
        # input budget precisely when they need it.
        # Metadata-only runs emit no rewrite, so they need far less output room —
        # which leaves more of the context window for the note itself.
        produces_polish = force_polish or not (metadata_only or large or quick)
        prompt = self._fit_input(system_prompt, prompt, produces_polish=produces_polish)
        messages = [{"role": "system", "content": [{"type": "text", "text": system_prompt}]}]
        # Output cap. The context fix (moderate model_max_num_tokens) is what
        # restored speed — NOT a tight output cap, which profiling showed saves
        # little time while risking truncation of the polished body (emitted LAST,
        # so it's the first thing lost). Repeated attempts to predict a tight cap
        # kept cutting real enrichments off. So we give generous room: use the
        # whole output budget, only reduced for metadata-only notes (which emit
        # no polished body and genuinely need little). The salvage parser remains
        # a backstop, but the goal is that it never has to fire.
        if produces_polish:
            gen_cap = self._max_tokens          # full budget; polish needs room
        else:
            gen_cap = min(self._max_tokens, 1024)   # metadata-only is small
        gen_cap = max(1024, gen_cap)
        # Be honest in the log: the cap is only ENFORCED on the session path.
        # On the proven create_conversation path the library manages generation
        # length itself, so reporting a hard cap there would be misleading.
        if self._gen_mechanism:
            log.info(f"    output cap {gen_cap} tok, enforced "
                     f"({word_count} words, polish={'y' if produces_polish else 'n'})")
        else:
            log.info(f"    generating ({word_count} words, "
                     f"polish={'y' if produces_polish else 'n'})")
        t0 = time.monotonic()
        try:
            text = self._generate(messages, prompt, cap=gen_cap)
            result = self._parse(text)
        except Exception as e:
            # Hard backstop for input-token overflow. The char-based fit estimate
            # can under-count on token-dense text; if the model rejects the input
            # as too long, trim the note AGGRESSIVELY (half the previous budget)
            # and retry ONCE. This can't be defeated by a wrong chars/token ratio
            # the way the estimate alone can — it reacts to the model's actual
            # verdict. Better a shorter note that enriches than a hard failure.
            if self._is_token_overflow(e):
                log.warning("    input still too long after fit — hard-trimming "
                            "and retrying once")
                prompt = self._hard_trim(system_prompt, prompt)
                try:
                    text = self._generate(messages, prompt, cap=gen_cap)
                    result = self._parse(text)
                except Exception as e2:
                    log.error(f"inference error (after retry): {e2}")
                    return None
            else:
                log.error(f"inference error: {e}")
                return None
        if result is None:
            return None
        result = self._normalize(result)
        if not str(result.get("title") or "").strip():
            # A blank title (the model emitted whitespace, or only tags survived): never store that as the title.
            first = next((ln.strip().lstrip("#-*• ").strip() for ln in str(note_text).splitlines() if ln.strip()), "")
            result["title"] = (first[:60].rstrip() + ("…" if len(first) > 60 else "")) if first else "Untitled note"
        result["_latency_s"] = round(time.monotonic() - t0, 3)
        return result

    @staticmethod
    def _norm_for_compare(s: str) -> str:
        """Lowercase and strip all non-alphanumeric chars, so two strings that
        differ only in punctuation/case/spacing compare equal."""
        return "".join(ch for ch in s.lower() if ch.isalnum())

    @staticmethod
    def _normalize(d: dict) -> dict:
        """Coerce model output into the expected types so downstream code is
        never surprised by a string where it wants a list, etc."""
        d.setdefault("title", "")
        d.setdefault("summary", "")
        d.setdefault("polished", "")
        # Defensive: strip any prompt-context the model may have echoed back
        # into its polished/summary text (small models sometimes regurgitate
        # the soft hints). Drop lines that are clearly instruction leakage.
        import re as _re
        _LEAK = _re.compile(
            r"(?im)^.*\b(context hints? (provided|only)|existing tags in this "
            r"notebook|tags already used in this notebook|tag vocabulary|"
            r"soft hints? only|source of truth|prefer reusing|prefer an existing "
            r"tag|consistent tags are what link|invent a new tag)\b.*$")
        for _k in ("polished", "summary"):
            _v = d.get(_k)
            if isinstance(_v, str) and _v:
                _cleaned = _LEAK.sub("", _v)
                # collapse the blank lines the removal may leave behind
                _cleaned = _re.sub(r"\n{3,}", "\n\n", _cleaned).strip()
                d[_k] = _cleaned
        # Defensive: drop a title-like line the model sometimes prepends to the
        # polished body ("Meeting Summary", "### Notes", "**Overview**"). The app
        # shows the `title` field above the note, so a heading here is pure
        # duplication. We only strip a SHORT first line that has no sentence
        # punctuation and is followed by a blank line — i.e. unmistakably a
        # heading, never a real opening sentence.
        _p = d.get("polished")
        if isinstance(_p, str) and _p.strip():
            _parts = _p.strip().split("\n", 1)
            if len(_parts) == 2:
                _first = _parts[0].strip()
                _bare = _re.sub(r"^#{1,6}\s*|^\*\*|\*\*$", "", _first).strip()
                _looks_heading = (
                    0 < len(_bare.split()) <= 5
                    and not _re.search(r"[.!?,;:]$", _bare)
                    and not _bare.startswith(("-", "*", ">", "|", "`"))
                    and _parts[1].lstrip("\n") != _parts[1]   # blank line follows
                )
                if _looks_heading:
                    d["polished"] = _parts[1].lstrip("\n").strip()
        # Lists: keep only non-empty, stripped string entries — so a stray [""]
        # or ["  "] from the model never renders as an empty bullet/tag.
        def _clean_str_list(v):
            if not isinstance(v, list):
                return []
            out = []
            for item in v:
                if isinstance(item, str):
                    s = item.strip()
                    # Drop prompt-instruction leakage the model sometimes emits
                    # as a fake action item (e.g. "Set 'polished' to an empty
                    # string", "provide title, summary, tags and action_items").
                    if s and not _re.search(
                        r"(?i)\b(set\s+[\"']?polished|empty string|provide (a )?(title|summary|tags)"
                        r"|action_items|context hints?|only provide|source of truth)\b", s):
                        out.append(s)
            return out
        d["tags"] = _clean_str_list(d.get("tags"))
        # action_items: the prompt asks for {task, category, blocked_by} objects. Flatten to task strings
        # (what the rest of the daemon expects) and keep category / blocker in action_meta.
        _acts, _meta = [], {}
        for _it in (d.get("action_items") if isinstance(d.get("action_items"), list) else []):
            if isinstance(_it, dict):
                _t = str(_it.get("task") or _it.get("text") or "").strip()
                _m = {"category": str(_it.get("category") or "").strip().lower(),
                      "blocked_by": str(_it.get("blocked_by") or "").strip()}
            elif isinstance(_it, str):
                _t = _it.strip(); _m = None
            else:
                continue
            if _t and _t.lower() not in _SCHEMA_WORDS and _clean_str_list([_t]):
                _acts.append(_t)
                if _m: _meta[_t] = _m
        d["action_items"] = _acts; d["action_meta"] = _meta
        if not isinstance(d.get("reminders"), list):
            d["reminders"] = []
        # corrections: list of {from,to,confidence}; tolerate malformed entries.
        # Guard against the model dumping whole-sentence/whole-note "corrections":
        #   • drop any entry whose 'from' exceeds MAX_CORRECTION_WORDS (it's a
        #     reflow/rewrite, not a discrete substitution — already visible in the
        #     enriched body, and a token sink).
        #   • drop no-ops: from==to once punctuation/case/whitespace are ignored
        #     (pure formatting changes are not meaning-level corrections).
        MAX_CORRECTION_WORDS = 15
        corr = d.get("corrections")
        if not isinstance(corr, list):
            corr = []
        clean = []
        for c in corr:
            if not (isinstance(c, dict) and c.get("from") and c.get("to")):
                continue
            frm, to = str(c["from"]).strip(), str(c["to"]).strip()
            if len(frm.split()) > MAX_CORRECTION_WORDS:
                continue  # oversized: a rewrite, not a correction
            if GemmaEngine._norm_for_compare(frm) == GemmaEngine._norm_for_compare(to):
                continue  # no-op: only punctuation/case/spacing changed
            try:
                cf = float(c.get("confidence", 0.7))
            except (TypeError, ValueError):
                cf = 0.7
            clean.append({"from": frm, "to": to,
                          "confidence": max(0.0, min(1.0, cf))})
        d["corrections"] = clean
        pr = str(d.get("priority", "")).strip().lower()
        d["priority"] = pr if pr in ("high", "medium", "low") else "low"
        try:
            c = float(d.get("confidence", 0.7))
            d["confidence"] = max(0.0, min(1.0, c))
        except (TypeError, ValueError):
            d["confidence"] = 0.7
        return d

    @staticmethod
    def _parse(text):
        text = re.sub(r"^```json?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        # strict=False: a literal newline or tab inside a string value (small models do this in "polished") is
        # not valid JSON, and used to push a complete answer down the "truncated" salvage path.
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(), strict=False)
                except Exception:
                    pass
        # SALVAGE a truncated response. Large notes can exhaust the output token
        # budget mid-JSON (the response is cut off with no closing brace), which
        # would otherwise fail the whole enrichment. Rather than lose everything,
        # extract whatever complete fields we can — a title + tags are enough to
        # be useful, and we drop the (possibly half-written) polished rewrite.
        salvaged = {}
        # String fields: "key": "value"  (value may run to end-of-string if the
        # closing quote was cut off — capture up to the last quote or line end).
        for key in ("title", "summary", "polished", "priority"):
            m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if m and m.group(1).strip():
                salvaged[key] = m.group(1).encode().decode("unicode_escape", "ignore")
        # Array fields: "tags": ["a", "b"  (may be unterminated — grab the items
        # that fully appear before the truncation).
        for key in ("tags", "action_items"):
            m = re.search(rf'"{key}"\s*:\s*\[([^\]]*)', text)
            if m:
                body = m.group(1)
                if key == "action_items" and re.search(r'"(?:task|text)"\s*:', body):
                    items = [x.encode().decode("unicode_escape", "ignore") for x in
                             re.findall(r'"(?:task|text)"\s*:\s*"((?:[^"\\]|\\.)*)"', body)]
                else:
                    items = [x for x in re.findall(r'"((?:[^"\\]|\\.)*)"', body) if key != "action_items" or x.strip().lower() not in _SCHEMA_WORDS]
                if items:
                    salvaged[key] = items
        if salvaged.get("title") or salvaged.get("tags"):
            # A truncated summary/polished may be half-written — drop them so we
            # never store a sentence that ends mid-word. Keep title + tags.
            salvaged.pop("polished", None)
            if isinstance(salvaged.get("summary"), str) and not salvaged["summary"].rstrip().endswith((".", "!", "?")):
                salvaged.pop("summary", None)
            log.warning(f"model output did not parse as JSON — salvaged title/tags "
                        f"(title={salvaged.get('title', '')[:40]!r}, {len(text)} chars, "
                        f"starts {' '.join(text.split())[:120]!r}, ends {' '.join(text.split())[-80:]!r})")
            return salvaged
        log.warning(f"unparseable model output: {text[:160]}")
        return None

    def multimodal(self, parts: list, prompt: str) -> str:
        """Ask the model about a photo or an audio clip. `parts` are content
        dicts like {"type": "image", "path": "..."} / {"type": "audio", ...}.

        Raises RuntimeError when this build/model can't take that input — the
        caller treats that as "unsupported" rather than retrying forever."""
        if not getattr(self, "_mm_enabled", False):
            raise RuntimeError("model was not loaded with vision/audio support")
        content = list(parts) + [{"type": "text", "text": prompt}]
        try:
            with self._engine.create_conversation() as conv:
                raw = conv.send_message({"role": "user", "content": content})
        except Exception as e:
            raise RuntimeError(f"multimodal call failed: {type(e).__name__}: {e}")
        return (self._responses_text(raw) or "").strip()

    def complete(self, prompt: str, max_tokens: int = 200,
                 system_prompt: str | None = None) -> str:
        """Raw single-turn completion for non-JSON tasks (e.g. the daily brief,
        Ask your notes). Uses a neutral system prompt so the enrichment schema
        doesn't leak in, unless the caller supplies its own."""
        messages = [{"role": "system", "content": [{"type": "text",
                     "text": system_prompt or "You are a helpful, concise assistant."}]}]
        try:
            return self._generate(messages, prompt, cap=max_tokens)
        except Exception as e:
            log.error(f"completion error: {e}")
            return ""

    def _fit_ask_context(self, question: str, excerpts: list[dict], extra: str = "",
                         system: str = ASK_SYSTEM_PROMPT, headroom: int | None = None) -> str:
        """Trim excerpts to fit the context window, using the same measured
        chars-per-token approach _fit_input uses for enrichment, but sized for
        Ask's own small output (a few sentences, not a polished rewrite) —
        see config ask_max_tokens/ask_output_headroom. Keeps at least the
        first (highest-ranked) excerpt even if it alone crowds the budget,
        same principle as _fit_input never returning empty input."""
        total = self._max_context
        out_headroom = int(headroom if headroom is not None
                           else self._cfg.get("ask_output_headroom", 350))
        sys_ratio = _chars_per_token(system)
        sys_tokens = len(system) / sys_ratio
        q_ratio = _chars_per_token(question + extra)
        q_tokens = len(question + extra) / q_ratio
        room = max(256.0, total - out_headroom - 64 - sys_tokens - q_tokens)
        kept, used = [], 0.0
        for i, ex in enumerate(excerpts, 1):
            # Numbered so the model can cite "[n]" — a small model copies a
            # digit far more reliably than an exact title.
            block = f"### [{i}] {ex.get('title', 'Untitled')}\n{ex.get('text', '')}\n"
            ratio = _chars_per_token(block)
            tok = len(block) / ratio
            if used + tok > room and kept:
                break
            kept.append(block)
            used += tok
        self._ask_kept = len(kept)     # how many numbered notes the model saw
        return "".join(kept)

    @staticmethod
    def _format_history(history) -> str:
        """The last couple of Q/A turns, as plain text for the prompt. Capped hard:
        it competes with the note excerpts for a small context window."""
        lines = []
        for t in (history or [])[-2:]:
            q = (t.get("question") or "").strip()[:200]
            a = (t.get("answer") or "").strip()[:300]
            if q and a:
                lines.append(f"Q: {q}\nA: {a}")
        return ("Earlier in this conversation:\n" + "\n".join(lines) + "\n\n") if lines else ""

    def ask(self, question: str, excerpts: list[dict], history=None) -> str:
        """Answer a question grounded in already-retrieved note excerpts (the
        PWA does retrieval/ranking — this never re-searches). Returns the raw
        model text; the caller (daemon._process_ask) splits the trailing
        SOURCES: line back into cited note ids. Runs through complete(), so it
        shares the same single-inference-at-a-time discipline as enrich()."""
        hist = self._format_history(history)
        context = self._fit_ask_context(question, excerpts, extra=hist)
        prompt = f"{hist}Notes:\n\n{context}\nQuestion: {question}\n"
        cap = int(self._cfg.get("ask_max_tokens", 300))
        return self.complete(prompt, max_tokens=cap, system_prompt=ASK_SYSTEM_PROMPT)

    def chat(self, prompt: str, history=None) -> str:
        """A plain prompt to the model — no notes involved."""
        hist = self._format_history(history)
        return self.complete(f"{hist}{prompt}\n", max_tokens=int(self._cfg.get("chat_max_tokens", 600)),
                             system_prompt=CHAT_SYSTEM_PROMPT)

    def story(self, description: str, outline: str = "", genre: str = "", length: str = "short",
              lang: str = "en", note_text: str = "", revise: str = "", instruction: str = "") -> str:
        """A short story from a photo's description (+ optional user outline). Returns
        "# Title\\n\\nstory…" — the title line is parsed out of the model's TITLE: line."""
        words = 380 if length == "medium" else 180
        cap = int(self._cfg.get("story_tokens_medium" if length == "medium" else "story_tokens_short",
                                800 if length == "medium" else 380))
        if lang == "te":
            cap = int(cap * 2.2)               # Telugu script costs several times more tokens per word
        if revise.strip():
            cap = max(cap, int(len(revise) / (1.5 if lang == "te" else 3.5)) + 150)
            ask = ("Photo description:\n" + description.strip()[:900] + "\n\nHere is the current story:\n\n" + revise.strip()[:5000]
                   + "\n\nRewrite the whole story applying this change from the user: " + instruction.strip()[:300]
                   + "\nKeep everything else that works. Same language as the current story."
                   + (f"\nOverall length: about {words} words." if length else "")
                   + "\nReturn the complete revised story in the usual format (TITLE: line, blank line, paragraphs).")
            raw = (self.complete(ask + "\n", max_tokens=cap, system_prompt=STORY_SYSTEM_PROMPT) or "").strip()
            return self._parse_story(raw)
        lines = [f"Photo description:\n{description.strip()[:1800]}" if description.strip() else
                 "Photo description: not available — the photo could not be described. Write only from the user's "
                 "outline and words below; do not invent what the photo shows beyond them."]
        if note_text.strip():
            lines.append(f"The user's own words about it: {note_text.strip()[:400]}")
        lines.append("User's story outline: " + (outline.strip() or "none — invent a fitting story"))
        lines.append(f"Genre: {genre or 'any — choose what suits the scene'}")
        lines.append(f"Length: about {words} words")
        lines.append("Language: " + ("Telugu — write the whole story in Telugu script (తెలుగు)" if lang == "te" else "English"))
        raw = (self.complete("\n".join(lines) + "\n", max_tokens=cap, system_prompt=STORY_SYSTEM_PROMPT) or "").strip()
        return self._parse_story(raw)

    @staticmethod
    def _parse_story(raw: str) -> str:
        if not raw:
            return ""
        m = re.match(r"(?is)^\W*title\s*[:\-]\s*(.+?)\s*\n+(.*)$", raw)
        if m:
            title, body = m.group(1).strip(" *#\"'"), m.group(2).strip()
        else:
            title, body = "", raw
        body = re.sub(r"(?m)^[#*]+\s*", "", body).strip()
        return (f"# {title}\n\n{body}" if title else body)

    def write(self, request: str, excerpts: list[dict]) -> str:
        """Long-form writing (blog post, white paper, guide...) from retrieved notes.
        A small on-device model writes far better one section at a time than in one
        long pass, so: plan a title + sections, then write each section with the
        notes in view and the previous section's ending for continuity."""
        sec_tok = int(self._cfg.get("write_section_tokens", 320))
        max_sec = int(self._cfg.get("write_sections_max", 6))
        ctx = self._fit_ask_context(request, excerpts, system=WRITE_SYSTEM_PROMPT, headroom=sec_tok + 200)
        base = f"Notes:\n\n{ctx}\nRequest: {request}\n"
        plan = self.complete(
            base + "\nPlan the document. First line: TITLE: <a good title>. Then 4 to 6 section "
                   "headings, one per line, each starting with '- '. Plan only; do not write the sections.",
            max_tokens=160, system_prompt=WRITE_SYSTEM_PROMPT)
        title, heads = "", []
        for ln in (plan or "").splitlines():
            ln = ln.strip()
            m = re.match(r"(?i)^\W*title\s*[:\-]\s*(.+)$", ln)
            if m and not title:
                title = m.group(1).strip(" *#\"'"); continue
            m = re.match(r"^(?:[-*•]|\d+[.)])\s+(.+)$", ln)
            if m:
                h = re.sub(r"^[#*\s]+|[*\s]+$", "", m.group(1)).strip(" :")
                if 2 < len(h) <= 80:
                    heads.append(h)
        heads = heads[:max_sec]
        if len(heads) < 2:                       # planning failed — one straight pass
            out = self.complete(base + "\nWrite the complete document now, with a title and short headed sections.",
                                max_tokens=int(self._cfg.get("write_max_tokens", 900)),
                                system_prompt=WRITE_SYSTEM_PROMPT)
            return (out or "").strip()
        outline = "\n".join(f"- {h}" for h in heads)
        parts, tail = [f"# {title}" if title else ""], ""
        for h in heads:
            cont = f"The previous section ended: \"…{tail}\"\n\n" if tail else ""
            sec = self.complete(
                base + f"\nOutline:\n{outline}\n\n{cont}Now write ONLY the section titled \"{h}\": about "
                       "120-180 words, no heading line, citing notes as [n].",
                max_tokens=sec_tok, system_prompt=WRITE_SYSTEM_PROMPT)
            sec = re.sub(r"(?m)^#+\s.*$", "", (sec or "")).strip()
            if not sec:
                continue
            tail = sec[-220:].replace("\n", " ")
            parts.append(f"## {h}\n\n{sec}")
        body = "\n\n".join(p for p in parts if p)
        return body if len(parts) > 1 else ""

    def close(self):
        try:
            self._engine.__exit__(None, None, None)
        except Exception:
            pass
