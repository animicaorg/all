# animica

Meta-package that installs the full Animica toolchain in one command.

```sh
npm install animica
```

This pulls in:

- [`animica-node`](https://www.npmjs.com/package/animica-node) — full-node operator (`animica-node` binary)
- [`animica-agent`](https://www.npmjs.com/package/animica-agent) — coding-agent CLI (`animica-agent` binary)
- [`@animica/agent-core`](https://www.npmjs.com/package/@animica/agent-core) — shared core library
- [`@animica/agent-sdk`](https://www.npmjs.com/package/@animica/agent-sdk) — typed SDK for embedding the agent
- [`@animica/agent-ui`](https://www.npmjs.com/package/@animica/agent-ui) — local browser dashboard

After installation you can run:

```sh
npx animica-node       # start the full node
npx animica-agent      # launch the coding agent CLI
```

## License

Apache-2.0
