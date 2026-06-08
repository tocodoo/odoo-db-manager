import {Interaction} from "@web/public/interaction";
import {registry} from "@web/core/registry";

export class SWdSnippet extends Interaction {
    static selector = ".s_wd_snippet";

    onClickButton(ev) {
        console.log("Button clicked!", ev.target);
    }

    events = {
        "click .btn": "onClickButton",
    };
}

registry.category("public.interactions").add("website_scaffold.s_wd_snippet", SWdSnippet);
