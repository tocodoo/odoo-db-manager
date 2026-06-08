/** @odoo-module **/

import options from "@web_editor/js/editor/snippets.options";

const YourSnippetOptions = options.Class.extend({
    selector: '.s_wd_snippet',
    start: function() {
        this._super.apply(this, arguments);
    },

    cleanForSave: function () {
        // This will be executed when you save the page.
    }
});

options.registry.slider = YourSnippetOptions;

export default YourSnippetOptions;
