# Third-Party Licenses & Notices

This service depends on open-source software. We acknowledge and thank the authors
and communities of these projects. License texts or references for the primary
runtime dependencies are listed below. The absence of a notice for a transitive
dependency does not waive any terms; consult the installed package metadata for
the complete set.

> This file is provided for informational purposes only. All third-party software
> remains copyrighted by its respective owners and licensed under the terms shown.

---

## Dependency Summary

| Package | Version (pinned) | License | Homepage |
|---|---:|---|---|
| **fastapi** | 0.114.2 | MIT | https://fastapi.tiangolo.com/ |
| **starlette** *(indirect)* | — | BSD-3-Clause | https://www.starlette.io/ |
| **uvicorn** | 0.30.6 | BSD-3-Clause | https://www.uvicorn.org/ |
| **gunicorn** | 22.0.0 | MIT | https://gunicorn.org/ |
| **pydantic** | 2.9.2 | MIT | https://docs.pydantic.dev/ |
| **pydantic-settings** | 2.5.2 | MIT | https://github.com/pydantic/pydantic-settings |
| **httpx** | 0.27.2 | BSD-3-Clause | https://www.python-httpx.org/ |
| **msgspec** | 0.18.6 | BSD-3-Clause | https://jcristharif.com/msgspec/ |
| **cbor2** | 5.6.4 | MIT | https://github.com/agronholm/cbor2 |
| **python-dotenv** | 1.0.1 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| **structlog** | 24.4.0 | Apache-2.0 | https://www.structlog.org/ |
| **prometheus-client** | 0.20.0 | Apache-2.0 | https://github.com/prometheus/client_python |

If you build from source and enable optional local packages (e.g., Animica SDK/VM),
please consult their corresponding LICENSE files in the repository root.

---

## License Texts (or References)

### MIT License
The following packages are licensed under MIT, reproduced here in summary form; see
their repositories for full texts if not included below:
- fastapi, gunicorn, pydantic, pydantic-settings, cbor2

> MIT License  
> Copyright (c) the respective authors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

### BSD 3-Clause License
The following packages are licensed under BSD-3-Clause:
- starlette, uvicorn, httpx, msgspec, python-dotenv

> BSD 3-Clause License  
> Copyright (c) the respective authors  
> All rights reserved.
>
> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are met:
>
> 1. Redistributions of source code must retain the above copyright notice, this
>    list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
> 3. Neither the name of the copyright holder nor the names of its
>    contributors may be used to endorse or promote products derived from
>    this software without specific prior written permission.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
> AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
> DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
> FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
> DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
> SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
> CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
> OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
> OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---

### Apache License 2.0
The following packages are licensed under Apache-2.0:
- structlog, prometheus-client

> Apache License  
> Version 2.0, January 2004  
> http://www.apache.org/licenses/
>
> TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
>
> (The complete text is available at the URL above. In brief, you may use,
> reproduce, and distribute the Work under the terms of the License, provided
> that you include a copy of the License and provide required notices. The Work
> is provided on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.)

---

## How this file is generated

This document was curated against the pinned versions in `requirements.txt`. For a
fully reproducible inventory, you can emit an SBOM or license summary locally:

```bash
pip-licenses --format=markdown --with-license-file --with-authors \
  --packages fastapi,uvicorn,gunicorn,pydantic,pydantic-settings,httpx,msgspec,cbor2,python-dotenv,structlog,prometheus-client

(Note: pip-licenses is an optional dev tool and not required to run the service.)

⸻

