import {FooterTemplateChoice} from "@website/builder/plugins/options/footer_template_option";
import {Plugin} from "@html_editor/plugin";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";

export class ScaffoldFooterOptionPlugin extends Plugin {
    static id = "scaffoldFooterOption";
    resources = {
        footer_templates_providers: () => [
            {
                key: "scaffold",
                Component: FooterTemplateChoice,
                props: {
                    title: _t("Scaffold"),
                    view: "website_scaffold.footer",
                    varName: "Scaffold",
                    imgSrc: "/website_scaffold/static/src/img/wbuilder/template_wbuilder_opt.svg",
                },
            },
        ],
    };
}

registry.category("website-plugins").add(ScaffoldFooterOptionPlugin.id, ScaffoldFooterOptionPlugin);
