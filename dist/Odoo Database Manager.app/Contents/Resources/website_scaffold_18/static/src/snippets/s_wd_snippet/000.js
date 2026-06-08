/** @odoo-module **/
import publicWidget from "@web/legacy/js/public/public_widget";

const YourSnippet = publicWidget.Widget.extend({
    selector: '.s_wd_snippet',
    init: function() {
        // This will be executed at the initialization of the snippet.
        this._super.apply(this, arguments);
    },

    willStart: async function() {
        const res = this._super(...arguments);
        // This will be executed before start() function.
        return Promise.all([res]);
    },

    _myCustomPrivateFunction: function() {
        console.log('Hello World');
    },

    start: function() {
        this._myCustomPrivateFunction();
        return this._super.apply(this, arguments);
    }
});

publicWidget.registry.YourSnippet = YourSnippet;

export default YourSnippet;