# Corresponding Source availability notice

The PaperTrans-specific adapter in `worker/` is licensed under Apache-2.0.
The bundled/forked pdf2zh-next engine and BabelDOC are licensed under
AGPL-3.0, and PyMuPDF is available under AGPL-3.0 or a commercial license.
Their copyright notices and license terms remain controlling.

For every PaperTrans BabelDOC worker image distributed by the PaperTrans
project, the exact patched engine source and build/install information are made
available without charge in both of these forms:

1. inside the image at `/opt/papertrans/corresponding-source/`; and
2. in the matching PaperTrans repository revision under `workers/babeldoc/`,
   together with the upstream revision, patch, dependency locks, Dockerfile,
   manifests, SBOM instructions, and adapter source.

The public project location is
<https://github.com/tenxnet/PaperTrans>. Use the immutable PaperTrans revision
recorded with the image release; a mutable default branch is not a substitute
for the matching source. If a distributed copy lacks its matching source,
request it through that repository's private security/contact channel. This
availability commitment is intended to remain valid for at least three years
after the corresponding image distribution.

Any downstream distributor or network operator must preserve the notices,
publish its own exact modified Corresponding Source, and provide remote users a
prominent source link when AGPL section 13 applies. Do not remove the embedded
source or bypass its readiness digest check. This notice describes the
project's source-availability procedure and is not legal advice.
