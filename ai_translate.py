"""Traduction de modules Odoo via un LLM (clé API ou CLI Claude Code)."""
import re
import shutil
import subprocess

from odoo_ops import _env_with_path

PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "default_model": "claude-sonnet-4-5"},
    "openai": {"label": "OpenAI (GPT)", "default_model": "gpt-4.1"},
    "gemini": {"label": "Google (Gemini)", "default_model": "gemini-2.5-pro"},
}

PROMPT_TEMPLATE = (
    "Translate the following Odoo .po file into {lang} (ISO code). "
    "Output ONLY the raw .po file content, starting directly with the first '#' comment line or "
    "the 'msgid \"\"' header line. "
    "Do not include any markdown code fence, preamble, reasoning, or explanation before or after the file content — "
    "your entire response must be valid .po syntax from the very first character.\n\n"
    "{po_content}"
)


def _build_prompt(po_content: str, lang: str) -> str:
    return PROMPT_TEMPLATE.format(lang=lang, po_content=po_content)


def _sanitize_po_output(text: str) -> str:
    """Retire tout ce qu'un LLM aurait pu ajouter autour du contenu .po attendu
    (blocs de code markdown, préambule/explication laissés malgré la consigne)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('msgid "'):
            if i > 0:
                lines = lines[i:]
            break
    return "\n".join(lines).strip() + "\n"


_PO_STRING_LINE_RE = re.compile(r'^"(?:[^"\\]|\\.)*"$')
_PO_KEYWORD_LINE_RE = re.compile(r'^(msgid|msgstr|msgctxt|msgid_plural)(\[\d+\])?\s+"(?:[^"\\]|\\.)*"$')


def _validate_po(text: str) -> "str | None":
    """Contrôle léger de syntaxe .po (sans dépendance externe type polib).

    Retourne un message d'erreur si une ligne ne respecte pas la grammaire .po
    (commentaire, mot-clé msgid/msgstr/msgctxt, ou ligne de continuation entre
    guillemets) — typiquement ce qui casse quand un LLM laisse une phrase de
    préambule ou une explication au milieu de sa réponse.
    """
    has_header = False
    for i, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _PO_KEYWORD_LINE_RE.match(line) or _PO_STRING_LINE_RE.match(line):
            if line.startswith('msgid ""'):
                has_header = True
            continue
        return f"ligne {i} ne respecte pas la syntaxe .po : {line[:100]!r}"
    if not has_header:
        return "en-tête .po manquant (msgid \"\" attendu en début de fichier)"
    return None


def translate_with_api_key(
    po_content: str, lang: str, provider: str, api_key: str, on_output: "callable | None" = None
) -> tuple[bool, str]:
    """Envoie le contenu .po au provider choisi et retourne (True, texte_traduit) ou (False, erreur)."""
    if not api_key:
        return False, "Clé API manquante."
    prompt = _build_prompt(po_content, lang)
    if on_output:
        on_output(f"→ Appel {provider} ({PROVIDERS.get(provider, {}).get('default_model', '?')})...")
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=PROVIDERS["anthropic"]["default_model"],
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
        elif provider == "openai":
            import openai
            client = openai.OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=PROVIDERS["openai"]["default_model"],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.choices[0].message.content
        elif provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(PROVIDERS["gemini"]["default_model"])
            resp = model.generate_content(prompt)
            raw = resp.text
        else:
            return False, f"Provider inconnu: {provider}"
    except Exception as e:
        if on_output:
            on_output(f"✗ {type(e).__name__}: {e}")
        return False, str(e)

    cleaned = _sanitize_po_output(raw)
    po_error = _validate_po(cleaned)
    if po_error:
        if on_output:
            on_output(f"✗ Réponse IA invalide (pas un .po valide) : {po_error}")
        return False, f"Le modèle n'a pas renvoyé un fichier .po valide : {po_error}"
    return True, cleaned


def validate_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Test minimal pour valider la clé avant de l'enregistrer."""
    ok, result = translate_with_api_key('msgid "Test"\nmsgstr ""', "fr_FR", provider, api_key)
    if ok:
        return True, "Clé API valide."
    return False, result


def find_claude_cli() -> str:
    env = _env_with_path()
    return shutil.which("claude", path=env.get("PATH", "")) or ""


def claude_cli_status() -> dict:
    path = find_claude_cli()
    return {"installed": bool(path), "path": path}


def _format_duration(seconds: float) -> str:
    seconds = max(1, round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rest = seconds % 60
    return f"{minutes}min{rest:02d}"


def estimate_translation_delay(n_chars: int) -> tuple[int, int]:
    """Estimation grossière (min, max) en secondes, basée sur la taille du prompt.

    Purement indicatif : dépend surtout de la charge du modèle, pas seulement
    de la taille du texte.
    """
    low = max(15, n_chars // 70)
    high = max(30, n_chars // 20)
    return low, high


def _run_claude_headless(prompt: str, timeout: int, on_output: "callable | None" = None) -> tuple[bool, str]:
    path = find_claude_cli()
    if not path:
        if on_output:
            on_output("✗ Claude Code CLI introuvable.")
        return False, "Claude Code CLI introuvable."
    if on_output:
        on_output(f"$ {path} -p --output-format text  (prompt: {len(prompt)} caractères)")
        low, high = estimate_translation_delay(len(prompt))
        on_output(f"≈ Délai estimé : {_format_duration(low)} - {_format_duration(high)} (variable selon la charge du modèle)")
    try:
        result = subprocess.run(
            [path, "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=_env_with_path(),
        )
    except subprocess.TimeoutExpired:
        if on_output:
            on_output("✗ Timeout lors de l'appel à Claude Code.")
        return False, "Timeout lors de l'appel à Claude Code."
    if result.returncode == 0 and result.stdout.strip():
        if on_output:
            on_output(f"✓ Réponse reçue ({len(result.stdout)} caractères).")
        cleaned = _sanitize_po_output(result.stdout)
        po_error = _validate_po(cleaned)
        if po_error:
            if on_output:
                on_output(f"✗ Réponse IA invalide (pas un .po valide) : {po_error}")
            return False, f"Le modèle n'a pas renvoyé un fichier .po valide : {po_error}"
        return True, cleaned
    error = (result.stderr or result.stdout or "Échec de l'appel à Claude Code.").strip()
    if on_output:
        on_output(f"✗ (code {result.returncode}) {error}")
    return False, error


def validate_claude_cli() -> tuple[bool, str]:
    """Vérifie que le CLI est installé et qu'une session Claude Code est bien active."""
    ok, result = _run_claude_headless("Reply with the single word: pong", timeout=60)
    if ok:
        return True, "Claude Code CLI opérationnel."
    return False, result


def translate_with_claude_cli(po_content: str, lang: str, on_output: "callable | None" = None) -> tuple[bool, str]:
    """Traduit le .po en passant par le CLI Claude Code déjà loggé (headless)."""
    return _run_claude_headless(_build_prompt(po_content, lang), timeout=600, on_output=on_output)
