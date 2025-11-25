# Localization (l10n)

This app uses **Flutter gen-l10n** with `.arb` files in `wallet/l10n/`.  
Strings are compiled into a strongly-typed `AppLocalizations` class.

---

## Quick start — add a new locale

1) **Create the ARB file**

Copy the base file and translate values:

```bash
cp wallet/l10n/intl_en.arb wallet/l10n/intl_fr.arb
# …edit intl_fr.arb values (keep the keys and @-metadata)
Guidelines:

Keep the same keys as English.

Update only the values; keep @metadata blocks intact.

Use placeholder names exactly as in English (e.g., {amount}, {symbol}).

Ensure codegen is enabled

pubspec.yaml should include:

yaml
Copy code
flutter:
  generate: true
Regenerate localizations

Either:

bash
Copy code
# If you have the helper script:
bash tool/gen_l10n.sh
or:

bash
Copy code
flutter gen-l10n
This generates files under lib/l10n/ (by default flutter_gen/gen_l10n/ import path).

Wire up in your app (once)

In MaterialApp:

dart
Copy code
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

MaterialApp(
  // ...
  localizationsDelegates: AppLocalizations.localizationsDelegates,
  supportedLocales: AppLocalizations.supportedLocales,
);
Use strings:

dart
Copy code
final l10n = AppLocalizations.of(context)!;
Text(l10n.navHome);
Placeholders & examples
Simple placeholder
Base (intl_en.arb)

json
Copy code
"balanceValue": "{amount} {symbol}",
"@balanceValue": {
  "description": "Formatted balance with symbol",
  "placeholders": {
    "amount": { "type": "String", "example": "12.34" },
    "symbol": { "type": "String", "example": "ANM" }
  }
}
Usage

dart
Copy code
l10n.balanceValue("12.34", "ANM");
Plurals
Add an ICU message:

json
Copy code
"itemsRemaining": "{count, plural, =0{None} one{# remaining} other{# remaining}}",
"@itemsRemaining": {
  "placeholders": { "count": { "type": "int", "example": 3 } }
}
Use:

dart
Copy code
l10n.itemsRemaining(3);
Select / enums
json
Copy code
"themeName": "{mode, select, light{Light} dark{Dark} other{System}}",
"@themeName": { "placeholders": { "mode": { "type": "String" } } }
Dates & numbers
For custom formatting use package:intl:

dart
Copy code
import 'package:intl/intl.dart';

final fmt = NumberFormat.decimalPattern(Localizations.localeOf(context).toString());
fmt.format(12345.67); // "12,345.67" (en-US)
RTL languages
For Arabic/Hebrew:

Create intl_ar.arb, etc.

Flutter will automatically set Directionality to RTL using the locale.

Validation & tips
Run flutter gen-l10n to catch missing keys/placeholders.

Keys must be identifiers: snakeCaseOrCamelCase, no spaces.

Do not translate placeholder names (inside {}).

Keep @metadata blocks; they help codegen and translator context.

Troubleshooting
“Missing translation” at runtime
Ensure the locale file exists (e.g., intl_fr.arb) and you re-ran codegen.

“Unsupported locale”
Check that supportedLocales includes your new locale (it’s auto-generated from ARBs).

Build fails after ARB edit
Validate JSON syntax (commas/quotes). Use a JSON linter if needed.

Adding many locales at once
Just drop more intl_xx.arb files and re-run codegen:

python-repl
Copy code
intl_en.arb
intl_es.arb
intl_fr.arb
intl_de.arb
...
Reference locations

ARBs: wallet/l10n/*.arb

Generated code (import): package:flutter_gen/gen_l10n/app_localizations.dart
