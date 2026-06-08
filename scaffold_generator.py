"""Generate customized website_* Odoo modules from the website_scaffold template."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

DEFAULT_AUTHOR = "Odoo PSBE"
TEMPLATE_MODULE = "website_scaffold"
TEMPLATE_LABEL = "Scaffold"

# Dossiers templates (le module interne s'appelle toujours website_scaffold avant renommage)
TEMPLATE_FOLDERS: dict[str, str] = {
    "19": "website_scaffold",
    "18": "website_scaffold_18",
}
SUPPORTED_ODOO_VERSIONS = ("19", "18")

AVAILABLE_FONTS: dict[str, dict[str, str]] = {
    "Inter": {"family": "Inter", "fallback": "sans-serif", "url": "Inter:300,300i,400,400i,700,700i"},
    "Inter Tight": {
        "family": "Inter Tight",
        "fallback": "sans-serif",
        "url": "Inter+Tight:300,300i,400,400i,500,500i,700,700i",
    },
    "Roboto": {"family": "Roboto", "fallback": "sans-serif", "url": "Roboto:300,300i,400,400i,700,700i"},
    "Open Sans": {
        "family": "Open Sans",
        "fallback": "sans-serif",
        "url": "Open+Sans:300,300i,400,400i,700,700i",
    },
    "Source Sans Pro": {
        "family": "Source Sans Pro",
        "fallback": "sans-serif",
        "url": "Source+Sans+Pro:300,300i,400,400i,700,700i",
    },
    "Raleway": {"family": "Raleway", "fallback": "sans-serif", "url": "Raleway:300,300i,400,400i,700,700i"},
    "Noto Serif": {"family": "Noto Serif", "fallback": "serif", "url": "Noto+Serif:300,300i,400,400i,700,700i"},
    "Arvo": {"family": "Arvo", "fallback": "Times, serif", "url": "Arvo:300,300i,400,400i,700,700i"},
    "Caveat": {"family": "Caveat", "fallback": "cursive", "url": "Caveat:400,500,700"},
    "Poppins": {"family": "Poppins", "fallback": "sans-serif", "url": "Poppins:300,300i,400,400i,500,500i,700,700i"},
    "Montserrat": {
        "family": "Montserrat",
        "fallback": "sans-serif",
        "url": "Montserrat:300,300i,400,400i,500,500i,700,700i",
    },
    "Lato": {"family": "Lato", "fallback": "sans-serif", "url": "Lato:300,300i,400,400i,700,700i"},
    "Playfair Display": {
        "family": "Playfair Display",
        "fallback": "serif",
        "url": "Playfair+Display:400,400i,700,700i",
    },
}

LAYOUT_OPTIONS = ("full", "boxed", "framed", "postcard")
LINK_UNDERLINE_OPTIONS = ("never", "hover", "always")
HEADER_LINKS_STYLE_OPTIONS = (
    "default",
    "fill",
    "outline",
    "pills",
    "block",
    "border-bottom",
    "underline",
    "bold",
)

TEXT_SUFFIXES = {
    ".py",
    ".xml",
    ".scss",
    ".js",
    ".md",
    ".txt",
    ".json",
    ".html",
    ".svg",
}

DEFAULT_COLORS = {
    "o-color-1": "#714B67",
    "o-color-2": "#017E84",
    "o-color-3": "#F3F4F6",
    "o-color-4": "#FFFFFF",
    "o-color-5": "#111827",
}

DEFAULT_THEME_COLORS = {
    "success": "#00C35A",
    "danger": "#D72F3D",
    "warning": "#FFB82A",
    "info": "#2F72D7",
    "light": "#FFF2E9",
    "dark": "#505050",
}

DEFAULT_GRAY_COLORS = {
    "white": "#FFFFFF",
    "100": "#E6E7E8",
    "200": "#D1D2D4",
    "300": "#BCBDBF",
    "400": "#A8A9AC",
    "500": "#949598",
    "600": "#818285",
    "700": "#6D6E71",
    "800": "#58585A",
    "900": "#3A3A3B",
    "black": "#292929",
}


def normalize_odoo_version(raw: str) -> str:
    version = (raw or "19").strip()
    if version not in SUPPORTED_ODOO_VERSIONS:
        raise ValueError(f"Version Odoo non supportée : {version} (18 ou 19).")
    return version


def get_template_dir(odoo_version: str = "19") -> Path:
    version = normalize_odoo_version(odoo_version)
    folder = TEMPLATE_FOLDERS[version]
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent.parent / "Resources"
        bundled = base / folder
        if bundled.is_dir():
            return bundled
    root = Path(__file__).resolve().parent
    return root / folder


def normalize_module_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", (raw or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError("Nom de module invalide (lettres, chiffres et _ uniquement).")
    if slug[0].isdigit():
        slug = f"m_{slug}"
    return slug


def module_name(slug: str) -> str:
    return f"website_{slug}"


def theme_label_from_slug(slug: str) -> str:
    return slug.replace("_", " ").title()


def _scss_quote(value: str) -> str:
    return value.replace("'", "\\'")


def _hex_color(value: str, fallback: str) -> str:
    v = (value or "").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", v):
        return v
    return fallback


def _optional_scss(value: Any, unit: str = "") -> str:
    if value is None or value == "":
        return "null"
    if isinstance(value, (int, float)):
        return f"{value}{unit}" if unit else str(value)
    s = str(value).strip()
    if not s or s.lower() == "null":
        return "null"
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return f"{s}{unit}" if unit else s
    return s


def get_defaults() -> dict[str, Any]:
    return {
        "author": DEFAULT_AUTHOR,
        "fonts": list(AVAILABLE_FONTS.keys()),
        "layouts": list(LAYOUT_OPTIONS),
        "link_underlines": list(LINK_UNDERLINE_OPTIONS),
        "header_links_styles": list(HEADER_LINKS_STYLE_OPTIONS),
        "colors": dict(DEFAULT_COLORS),
        "theme_colors": dict(DEFAULT_THEME_COLORS),
        "gray_colors": dict(DEFAULT_GRAY_COLORS),
        "font": "Inter",
        "headings_font": "Caveat",
        "navbar_font": "Inter",
        "buttons_font": "Inter",
        "layout": "full",
        "link_underline": "hover",
        "header_font_size": "1rem",
        "logo_height": "1.5rem",
        "fixed_logo_height": "1rem",
        "btn_border_radius": "null",
        "btn_border_radius_sm": "null",
        "btn_border_radius_lg": "2rem",
        "btn_padding_x": "1rem",
        "btn_padding_x_lg": "2.5rem",
        "btn_padding_y_lg": "1rem",
        "menu": 2,
        "footer": 5,
        "odoo_version": "19",
        "odoo_versions": [
            {
                "value": "19",
                "label": "Odoo 19",
                "description": "html_builder, Interaction, website_builder_assets",
            },
            {
                "value": "18",
                "label": "Odoo 18",
                "description": "publicWidget, we-button options, assets_wysiwyg",
            },
        ],
        "template_dir": str(get_template_dir("19")),
        "templates": {
            v: str(get_template_dir(v)) for v in SUPPORTED_ODOO_VERSIONS
        },
    }


def _normalize_pages(pages: list[dict] | None, module: str) -> list[dict]:
    normalized: list[dict] = []
    seen_urls: set[str] = set()
    for idx, raw in enumerate(pages or []):
        name = (raw.get("name") or "").strip() or f"Page {idx + 1}"
        url = (raw.get("url") or "").strip() or "/"
        if not url.startswith("/"):
            url = f"/{url}"
        slug = (raw.get("slug") or "").strip().lower()
        slug = re.sub(r"[^a-z0-9_]", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_") or f"page_{idx + 1}"
        if url in seen_urls:
            raise ValueError(f"URL en double : {url}")
        seen_urls.add(url)
        normalized.append(
            {
                "name": name,
                "url": url,
                "slug": slug,
                "in_menu": bool(raw.get("in_menu", True)),
                "menu_name": (raw.get("menu_name") or "").strip() or name,
                "sequence": int(raw.get("sequence") or (10 + idx * 10)),
                "meta_title": (raw.get("meta_title") or "").strip() or name,
                "is_home": url in ("/", ""),
            }
        )
    if not normalized:
        normalized.append(
            {
                "name": "Home",
                "url": "/",
                "slug": "home",
                "in_menu": True,
                "menu_name": "Home",
                "sequence": 10,
                "meta_title": "Home",
                "is_home": True,
            }
        )
    if not any(p["is_home"] for p in normalized):
        normalized[0]["url"] = "/"
        normalized[0]["is_home"] = True
    return normalized


def _font_scss_block(font_name: str) -> str:
    cfg = AVAILABLE_FONTS.get(font_name)
    if not cfg:
        family = font_name.replace("'", "\\'")
        url = font_name.replace(" ", "+") + ":400,400i,700,700i"
        return f"""    '{_scss_quote(font_name)}': (
        'family':   ('{family}', sans-serif),
        'url':      '{url}',
    ),"""
    family = cfg["family"]
    fallback = cfg["fallback"]
    return f"""    '{_scss_quote(font_name)}': (
        'family':   ('{family}', {fallback}),
        'url':      '{cfg["url"]}',
    ),"""


def generate_primary_variables(theme: dict, theme_label: str) -> str:
    palette_name = _scss_quote(theme_label)
    colors = {**DEFAULT_COLORS, **(theme.get("colors") or {})}
    for key in DEFAULT_COLORS:
        colors[key] = _hex_color(colors.get(key, ""), DEFAULT_COLORS[key])

    theme_colors = {**DEFAULT_THEME_COLORS, **(theme.get("theme_colors") or {})}
    for key in DEFAULT_THEME_COLORS:
        theme_colors[key] = _hex_color(theme_colors.get(key, ""), DEFAULT_THEME_COLORS[key])

    gray_colors = {**DEFAULT_GRAY_COLORS, **(theme.get("gray_colors") or {})}
    for key in DEFAULT_GRAY_COLORS:
        gray_colors[key] = _hex_color(gray_colors.get(key, ""), DEFAULT_GRAY_COLORS[key])

    fonts_used = []
    for key in ("font", "headings_font", "navbar_font", "buttons_font"):
        val = (theme.get(key) or get_defaults()[key]).strip()
        if val and val not in fonts_used:
            fonts_used.append(val)

    font_blocks = "\n".join(_font_scss_block(f) for f in fonts_used)

    menu_idx = int(theme.get("menu") or 2)
    footer_idx = int(theme.get("footer") or 5)

    return f"""// PRESETS — generated by Odoo Database Manager
$o-website-values-palettes: (
    (
        'color-palettes-name':              '{palette_name}',
        'font':                             '{_scss_quote(theme.get("font") or "Inter")}',
        'headings-font':                    '{_scss_quote(theme.get("headings_font") or "Inter")}',
        'navbar-font':                      '{_scss_quote(theme.get("navbar_font") or "Inter")}',
        'buttons-font':                     '{_scss_quote(theme.get("buttons_font") or "Inter")}',
        'header-template':                  '{palette_name}',
        'header-font-size':                 {_optional_scss(theme.get("header_font_size"), "rem")},
        'logo-height':                      {_optional_scss(theme.get("logo_height"), "rem")},
        'fixed-logo-height':                {_optional_scss(theme.get("fixed_logo_height"), "rem")},
        'footer-template':                  '{palette_name}',
        'layout':                           '{theme.get("layout") or "full"}',
        'link-underline':                   '{theme.get("link_underline") or "hover"}',
        'header-links-style':               '{theme.get("header_links_style") or "default"}',
        'btn-border-radius':                {_optional_scss(theme.get("btn_border_radius"))},
        'btn-border-radius-sm':             {_optional_scss(theme.get("btn_border_radius_sm"))},
        'btn-border-radius-lg':             {_optional_scss(theme.get("btn_border_radius_lg"))},
        'btn-padding-x':                    {_optional_scss(theme.get("btn_padding_x"), "rem")},
        'btn-padding-x-lg':                 {_optional_scss(theme.get("btn_padding_x_lg"), "rem")},
        'btn-padding-y-lg':                 {_optional_scss(theme.get("btn_padding_y_lg"), "rem")},
    ),
);

// FONTS
$o-theme-font-configs: (
{font_blocks}
);

// COLORS
$o-color-palettes: map-merge($o-color-palettes,
    (
        '{palette_name}': (
            'o-color-1': {colors["o-color-1"]},
            'o-color-2': {colors["o-color-2"]},
            'o-color-3': {colors["o-color-3"]},
            'o-color-4': {colors["o-color-4"]},
            'o-color-5': {colors["o-color-5"]},
            'menu':      {menu_idx},
            'footer':    {footer_idx},
        ),
    ),
);

$o-user-gray-color-palette: (
    'white': {gray_colors["white"]},
    '100':   {gray_colors["100"]},
    '200':   {gray_colors["200"]},
    '300':   {gray_colors["300"]},
    '400':   {gray_colors["400"]},
    '500':   {gray_colors["500"]},
    '600':   {gray_colors["600"]},
    '700':   {gray_colors["700"]},
    '800':   {gray_colors["800"]},
    '900':   {gray_colors["900"]},
    'black': {gray_colors["black"]},
);

$o-user-theme-color-palette: (
    'success': {theme_colors["success"]},
    'danger':  {theme_colors["danger"]},
    'warning': {theme_colors["warning"]},
    'info':    {theme_colors["info"]},
    'light':   {theme_colors["light"]},
    'dark':    {theme_colors["dark"]},
);

$o-selected-color-palettes-names: append($o-selected-color-palettes-names, '{palette_name}');
"""


def generate_page_xml(page: dict, module: str) -> str:
    slug = page["slug"]
    meta = page["meta_title"]
    return f"""<?xml version="1.0" encoding="utf-8"?>
<odoo noupdate="1">
    <record id="page_{slug}" model="website.page">
        <field name="name">{page["name"]}</field>
        <field name="is_published" eval="True"/>
        <field name="key">{module}.page_{slug}</field>
        <field name="url">{page["url"]}</field>
        <field name="type">qweb</field>
        <field name="website_id" eval="1"/>
        <field name="arch" type="xml">
            <t name="{page["name"]}" t-name="{module}.page_{slug}">
                <t t-call="website.layout">
                    <t t-set="additional_title" t-valuef="{meta}"/>
                    <t t-set="pageName" t-valuef="x_wd_page_{slug}"/>
                    <div id="wrap" class="oe_structure oe_empty"/>
                </t>
            </t>
        </field>
    </record>
</odoo>
"""


def generate_menu_xml(pages: list[dict], module: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<odoo noupdate=\"1\">",
        "    <delete model=\"website.menu\" search=\"[('url','in', ['/', '/']), ('website_id', '=', 1)]\"/>",
    ]
    for page in pages:
        if not page.get("in_menu"):
            continue
        lines.extend(
            [
                f'    <record id="menu_{page["slug"]}" model="website.menu">',
                f'        <field name="name">{page["menu_name"]}</field>',
                f'        <field name="url">{page["url"]}</field>',
                '        <field name="parent_id" search="[ (\'url\', \'=\', \'#\'), (\'website_id\', \'=\', 1)]"/>',
                "        <field name=\"website_id\">1</field>",
                f'        <field name="sequence" type="int">{page["sequence"]}</field>',
                "    </record>",
            ]
        )
    lines.append("</odoo>")
    return "\n".join(lines) + "\n"


def generate_website_xml(
    module: str,
    website_name: str,
    meta_title: str = "",
) -> str:
    meta_field = ""
    if meta_title.strip():
        meta_field = f"""
        <field name="social_default_image" eval="False"/>
        <!-- Default site title hint: {meta_title.strip()} -->"""
    return f"""<odoo>
    <record id="website.default_website" model="website">
        <field name="name">{website_name}</field>
        <field name="logo" type="base64" file="{module}/static/src/img/content/logo.svg"/>
        <field name="favicon" type="base64" file="{module}/static/description/favicon.png"/>{meta_field}
    </record>
</odoo>
"""


def _update_manifest(content: str, module: str, page_files: list[str], meta: dict) -> str:
    title = (meta.get("module_title") or meta.get("website_name") or module).replace("'", "\\'")
    author = (meta.get("author") or DEFAULT_AUTHOR).replace("'", "\\'")
    description = (meta.get("description") or f"Website theme — {title}").replace("'", "\\'")

    odoo_version = normalize_odoo_version(meta.get("odoo_version", "19"))
    version_str = f"{odoo_version}.0.0.0.0"

    content = re.sub(r"'name':\s*'[^']*'", f"'name': '{title}'", content, count=1)
    content = re.sub(r"'description':\s*'[^']*'", f"'description': '{description}'", content, count=1)
    content = re.sub(r"'author':\s*'[^']*'", f"'author': '{author}'", content, count=1)
    content = re.sub(r"'version':\s*'[^']*'", f"'version': '{version_str}'", content, count=1)
    content = content.replace(TEMPLATE_MODULE, module)

    page_lines = "\n".join(f"        'data/pages/{f}'," for f in page_files)
    content = re.sub(
        r"# Static pages\n(?:\s*'data/pages/[^']+',?\n)+",
        f"# Static pages\n{page_lines}\n",
        content,
    )
    return content


def _transform_text(content: str, module: str, theme_label: str) -> str:
    content = content.replace(TEMPLATE_MODULE, module)
    content = content.replace(TEMPLATE_LABEL, theme_label)
    return content


def generate_scaffold(config: dict) -> dict[str, Any]:
    slug = normalize_module_slug(config.get("module_slug") or config.get("module_name") or "")
    module = module_name(slug)
    theme_label = (config.get("theme_label") or "").strip() or theme_label_from_slug(slug)
    output_dir = Path((config.get("output_path") or "").strip()).expanduser().resolve()
    if not output_dir.is_dir():
        raise ValueError(f"Dossier de destination introuvable : {output_dir}")

    target = output_dir / module
    if target.exists():
        raise ValueError(f"Le module existe déjà : {target}")

    odoo_version = normalize_odoo_version(config.get("odoo_version", "19"))
    template_dir = get_template_dir(odoo_version)
    if not template_dir.is_dir():
        raise ValueError(f"Template scaffold Odoo {odoo_version} introuvable : {template_dir}")

    shutil.copytree(
        template_dir,
        target,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
    )

    pages = _normalize_pages(config.get("pages"), module)
    theme = config.get("theme") or {}
    page_files = [f"{p['slug']}.xml" for p in pages]

    (target / "static" / "src" / "scss" / "primary_variables.scss").write_text(
        generate_primary_variables(theme, theme_label),
        encoding="utf-8",
    )

    pages_dir = target / "data" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("*.xml"):
        old.unlink()
    for page, filename in zip(pages, page_files):
        (pages_dir / filename).write_text(generate_page_xml(page, module), encoding="utf-8")

    (target / "data" / "menu.xml").write_text(generate_menu_xml(pages, module), encoding="utf-8")
    (target / "data" / "website.xml").write_text(
        generate_website_xml(
            module,
            (config.get("website_name") or theme_label).strip(),
            (config.get("meta_title") or "").strip(),
        ),
        encoding="utf-8",
    )

    favicon_src = (config.get("favicon_path") or "").strip()
    if favicon_src:
        favicon_file = Path(favicon_src).expanduser()
        if favicon_file.is_file():
            shutil.copy2(favicon_file, target / "static" / "description" / "favicon.png")

    manifest_path = target / "__manifest__.py"
    manifest_path.write_text(
        _update_manifest(
            manifest_path.read_text(encoding="utf-8"),
            module,
            page_files,
            {**config, "odoo_version": odoo_version},
        ),
        encoding="utf-8",
    )

    readme = target / "README.md"
    if readme.is_file():
        readme.write_text(
            _transform_text(
                readme.read_text(encoding="utf-8"),
                module,
                theme_label,
            ),
            encoding="utf-8",
        )

    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "__manifest__.py":
            continue
        if path.name == "primary_variables.py":
            continue
        if path.parent.name == "pages" and path.suffix == ".xml":
            continue
        if path.name in ("menu.xml", "website.xml", "__manifest__.py") and path.parent.name == "data":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = _transform_text(text, module, theme_label)
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    return {
        "ok": True,
        "module": module,
        "path": str(target),
        "pages": len(pages),
        "theme_label": theme_label,
        "odoo_version": odoo_version,
    }
