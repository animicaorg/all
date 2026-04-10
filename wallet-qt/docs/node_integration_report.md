# Node Integration Report

This document is retained only as a historical placeholder.

`wallet-qt` no longer integrates an embedded node.

Current product direction:

- remote-RPC wallet only
- hosted endpoint `https://rpc.animica.org`
- no node lifecycle management in the Qt wallet

If you need node/operator workflows, use the dedicated Animica node tooling outside `wallet-qt`.
