# Third-Party Notices for `installers/`

This document lists third-party software and services that may be **invoked** by scripts in the `installers/` subproject (signing, verification, packaging). The `installers/` directory **does not redistribute** third-party binaries; it orchestrates tools that are typically preinstalled on CI runners or obtained from their vendors.

If your release pipeline adds or vendors additional tools, **append them here** with license info and (if applicable) embedded license texts.

---

## Summary (tools referenced by our scripts)

| Component / Tool                                                                 | Where used                                   | License / Terms                                                                                       |
|---|---|---|
| Apple Developer command-line tools: `codesign`, `productsign`, `productbuild`, `pkgutil`, `spctl`, `stapler`, `hdiutil` | macOS signing, package verification, notarization checks | Apple Software License / Apple Developer Program License. See Apple terms. |
| Microsoft PowerShell (`pwsh` / `powershell`) and .NET runtime                     | Cross-platform MSIX signature verification   | MIT License (PowerShell); .NET runtime under its respective licenses. |
| Windows `certutil.exe`                                                            | Importing/intermediate chain on Windows      | Microsoft Windows License Terms. |
| Windows SDK `signtool.exe` (optional if used in your pipeline)                    | Windows/MSIX code signing                     | Microsoft Windows SDK EULA. |
| GitHub Actions reusable actions (e.g., `actions/checkout`, `actions/cache`)       | CI workflow steps                             | MIT License (per action repository). |

> Notes
> - Our verification script prefers `Get-AuthenticodeSignature` (PowerShell) for MSIX validation; if you use `signtool.exe`, ensure you comply with the Windows SDK EULA.
> - Apple notarization may use API keys; App Store Connect and related services are governed by Apple’s terms.

---

## No bundled third-party code in `installers/`

All Bash/PowerShell scripts under `installers/scripts/` are authored for this project and released under the repository’s primary license unless noted inline. They **do not** embed third-party source code.

---

## License texts and attributions

### MIT License (applies to, for example, PowerShell and common GitHub Actions)

The following MIT text applies to the upstream projects listed above which are licensed under MIT (consult upstream repos for exact terms and copyright notices):

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

Upstream references:
- PowerShell: https://github.com/PowerShell/PowerShell (LICENSE: MIT)  
- actions/checkout: https://github.com/actions/checkout (LICENSE: MIT)  
- actions/cache: https://github.com/actions/cache (LICENSE: MIT)

### Apple tools and services

- `codesign`, `productsign`, `productbuild`, `pkgutil`, `spctl`, `stapler`, `hdiutil` are provided by Apple as part of macOS / Xcode Command Line Tools and are covered by Apple’s software licenses and developer terms.  
- Notarization and App Store Connect are governed by Apple agreements.  
- See Apple licensing/terms via the Apple Developer site.

### Microsoft tools and services

- `certutil.exe` is part of Windows; subject to Microsoft Windows License Terms.  
- `signtool.exe` is part of the Windows SDK; subject to the SDK EULA.  
- PowerShell binaries are MIT-licensed; the Windows-desktop edition may also include components under Microsoft terms. Consult upstream for specifics.

---

## How to add a new notice

1. Add a row to the summary table with **component**, **usage**, and **license**.  
2. If you vendor or redistribute any third-party binaries or source in this directory, include the **full license text** below, plus copyright attribution.
3. Keep URLs pointing to the authoritative upstream repos or vendor terms.

---

## Change log for this notice

- 1.0 — Initial list covering Apple/macOS tooling, Microsoft/Windows tools, PowerShell (MIT), and common GitHub Actions (MIT).

