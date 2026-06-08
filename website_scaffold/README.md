# Website Scaffold

Module thème de départ pour **Odoo 19** — structure alignée sur `website_ktm` (PSBE).

## Structure snippets (comme KTM)

```
views/snippets/
├── snippets.xml              # enregistrement panneau (groupes + structure + content)
└── s_wd_snippet.xml          # template QWeb du snippet

static/src/snippets/s_wd_snippet/
├── 000.scss                  # styles frontend
└── 000.js                    # Interaction publique (clic bouton)

static/src/website_builder/
├── s_wd_snippet_option.xml           # UI options builder
├── s_wd_snippet_option_plugin.js     # plugin website-plugins
├── header_template_option.xml
└── footer_template_option_plugin.js
```

## Conventions KTM respectées

| Élément | KTM | Scaffold |
|---------|-----|----------|
| Panneau snippets | `views/snippets/snippets.xml` | idem |
| XPath groupe | `snippet_groups/*[1]` | idem |
| XPath structure | `snippet_structure/*[1]` | idem |
| Snippets internes | `snippet_content/*[1]` | idem |
| Assets snippet | `000.scss` + `000.js` seulement | idem (pas de `000.xml`) |
| Options builder | `static/src/website_builder/*_option_plugin.js` | idem |
| Bundle builder | `website_builder/**/*` | idem |

## Installation

1. Ajoutez ce dossier à votre `addons_path`.
2. `-u website_scaffold`
3. Website → Edit → snippet **Scaffold Welcome** dans le groupe **Scaffold**.
