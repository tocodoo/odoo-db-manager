import {BaseOptionComponent} from "@html_builder/core/utils";
import {Plugin} from "@html_editor/plugin";
import {SNIPPET_SPECIFIC} from "@html_builder/utils/option_sequence";
import {registry} from "@web/core/registry";
import {withSequence} from "@html_editor/utils/resource";

export class SWdSnippetOption extends BaseOptionComponent {
    static template = "website_scaffold.SWdSnippetOption";
    static selector = ".s_wd_snippet";
}

class SWdSnippetOptionPlugin extends Plugin {
    static id = "scaffoldSWdSnippetOption";
    resources = {
        builder_options: [withSequence(SNIPPET_SPECIFIC, SWdSnippetOption)],
    };
}

registry.category("website-plugins").add(SWdSnippetOptionPlugin.id, SWdSnippetOptionPlugin);
