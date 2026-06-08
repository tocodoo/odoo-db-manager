{
    'name': 'Website Module Scaffold',
    'description': 'Description of the module',
    'version': '19.0.0.0.0',
    'author': 'Odoo PSBE Designers (Author\'s Tri/Quadrigram)',
    'license': 'OEEL-1',
    'depends': ['website'],
    'data': [
        # Website
        'data/website.xml',
        # Images
        'data/images.xml',
        # Menu
        'data/menu.xml',
        # Presets
        'data/presets.xml',
        # Static pages
        'data/pages/home.xml',
        # Views
        'views/snippets/snippets.xml',
        'views/snippets/s_wd_snippet.xml',
        'views/website_templates.xml'
    ],
    'assets': {
        'web._assets_bootstrap': [
            ('before', 'web/static/lib/bootstrap/scss/utilities/_api.scss', 'website_scaffold/static/src/scss/bootstrap_utilities.scss'),
        ],
        'web._assets_primary_variables': [
            'website_scaffold/static/src/scss/primary_variables.scss',
        ],
        'web._assets_frontend_helpers': [
            ('prepend', 'website_scaffold/static/src/scss/bootstrap_overridden.scss'),
        ],
        'website.website_builder_assets': [
            'website_scaffold/static/src/website_builder/**/*',
        ],
        'web.assets_frontend': [
            # LIB
            # Lib name
            # 'website_scaffold/static/src/lib/libname/libname.min.css',
            # 'website_scaffold/static/src/lib/libname/libname.min.js',
            # SCSS
            # Base
            'website_scaffold/static/src/scss/base/variables.scss',
            'website_scaffold/static/src/scss/base/functions.scss',
            'website_scaffold/static/src/scss/base/mixins.scss',
            'website_scaffold/static/src/scss/base/fonts.scss',
            'website_scaffold/static/src/scss/base/icons.scss',
            'website_scaffold/static/src/scss/bootstrap_latest.scss',
            'website_scaffold/static/src/scss/base/helpers.scss',
            'website_scaffold/static/src/scss/base/typography.scss',
            # Components
            'website_scaffold/static/src/scss/components/buttons.scss',
            # Layout
            'website_scaffold/static/src/scss/layout/body.scss',
            'website_scaffold/static/src/scss/layout/header.scss',
            'website_scaffold/static/src/scss/layout/footer.scss',
            'website_scaffold/static/src/scss/layout/blog.scss',
            # Pages
            'website_scaffold/static/src/scss/pages/home.scss',
            # Standard Snippets Overrides
            'website_scaffold/static/src/scss/snippets/cookies_bar.scss',
            # Global QWeb JS Templates
            'website_scaffold/static/src/xml/example.xml',
            # Custom Snippets
            'website_scaffold/static/src/snippets/s_wd_snippet/000.scss',
            'website_scaffold/static/src/snippets/s_wd_snippet/000.js',
        ],
    },
    'cloc_exclude': [
        'static/src/scss/bootstrap_overridden.scss',
        'static/src/scss/primary_variables.scss',
        'lib/**/*',
        'data/**/*'
    ],
}
