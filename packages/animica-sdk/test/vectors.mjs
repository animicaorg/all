// Ground-truth bech32m vectors generated with the Animica monorepo's own
// Python implementation (pq/py/utils/bech32.py + pq/py/address.py), run in
// /root/animica/.venv. Payloads are alg_id(2, BE) || sha3-256(pubkey)(32)
// for address vectors; algId === null marks generic payload round-trips.
export const VECTORS = {
  valid: [
    {
      address:
        "anim1zqphej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0sqdh2un",
      hrp: "anim",
      payloadHex:
        "10037ccb4ccc97518f33880a3343c803a9ace130aa5daac1372c4ac330a6a9b1e33e",
      algId: 4099,
    },
    {
      address:
        "anim1zqp8ej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0sn23dsa",
      hrp: "anim",
      payloadHex:
        "10027ccb4ccc97518f33880a3343c803a9ace130aa5daac1372c4ac330a6a9b1e33e",
      algId: 4098,
    },
    {
      address:
        "anim1zqp45zpdxzekwxjrcdzu68lftwytawt0rs39ywdj232y0cm43wnyxks2sa5hx",
      hrp: "anim",
      payloadHex:
        "10035a082d30b3671a43c345cd1fe95b88beb96f1c225239b2545447e3758ba6435a",
      algId: 4099,
    },
    {
      address:
        "anim1zqp95zpdxzekwxjrcdzu68lftwytawt0rs39ywdj232y0cm43wnyxksehmnmg",
      hrp: "anim",
      payloadHex:
        "10025a082d30b3671a43c345cd1fe95b88beb96f1c225239b2545447e3758ba6435a",
      algId: 4098,
    },
    {
      address:
        "anim1zqpnvtz2z8qaqt8j8lmq5uz7hew6p5paj0p698v6k6llqapd8wkln3gfp3cq3",
      hrp: "anim",
      payloadHex:
        "1003362c4a11c1d02cf23ff60a705ebe5da0d03d93c3a29d9ab6bff0742d3badf9c5",
      algId: 4099,
    },
    {
      address:
        "anim1zqprvtz2z8qaqt8j8lmq5uz7hew6p5paj0p698v6k6llqapd8wkln3g6xhlvl",
      hrp: "anim",
      payloadHex:
        "1002362c4a11c1d02cf23ff60a705ebe5da0d03d93c3a29d9ab6bff0742d3badf9c5",
      algId: 4098,
    },
    {
      address:
        "anim1zqpsl42jyqqm82z5nwxtd0f4jnegt8hczf4m6ytntzjjxf62ls7e9aqw7zpzy",
      hrp: "anim",
      payloadHex:
        "10030fd5522001b3a8549b8cb6bd3594f2859ef8126bbd117358a523274afc3d92f4",
      algId: 4099,
    },
    {
      address:
        "anim1zqpql42jyqqm82z5nwxtd0f4jnegt8hczf4m6ytntzjjxf62ls7e9aqaeyxw2",
      hrp: "anim",
      payloadHex:
        "10020fd5522001b3a8549b8cb6bd3594f2859ef8126bbd117358a523274afc3d92f4",
      algId: 4098,
    },
    {
      address:
        "anim1zqp4656xnus0aa8ca26jhzqyfm0xn3m6df52vpegvz0uffjl75c705qfxw9hv",
      hrp: "anim",
      payloadHex:
        "10035d53469f20fef4f8eab52b88044ede69c77a6a68a60728609fc4a65ff531e7d0",
      algId: 4099,
    },
    {
      address:
        "anim1zqp9656xnus0aa8ca26jhzqyfm0xn3m6df52vpegvz0uffjl75c705q6pgzmz",
      hrp: "anim",
      payloadHex:
        "10025d53469f20fef4f8eab52b88044ede69c77a6a68a60728609fc4a65ff531e7d0",
      algId: 4098,
    },
    {
      address:
        "anim1zqpcu759vdjl08jzqp92rfr68wp73eksa0dmvqhky7f72aqnnw0j59cvvlxth",
      hrp: "anim",
      payloadHex:
        "10038e7a856365f79e42004aa1a47a3b83e8e6d0ebdbb602f62793e574139b9f2a17",
      algId: 4099,
    },
    {
      address:
        "anim1zqpgu759vdjl08jzqp92rfr68wp73eksa0dmvqhky7f72aqnnw0j59cltep8e",
      hrp: "anim",
      payloadHex:
        "10028e7a856365f79e42004aa1a47a3b83e8e6d0ebdbb602f62793e574139b9f2a17",
      algId: 4098,
    },
    // Real on-chain address: the Animica foundation treasury (fork 42001).
    {
      address:
        "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga",
      hrp: "anim",
      payloadHex:
        "10030de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229",
      algId: 4099,
    },
    // Generic payload round-trips (not 34-byte addresses; algId null).
    { address: "anim1qvanku6d", hrp: "anim", payloadHex: "03", algId: null },
    {
      address: "anim1qv9pzxqlyckngw6zf9g9whn9d3eh4qvgwd58hp",
      hrp: "anim",
      payloadHex: "030a11181f262d343b424950575e656c737a8188",
      algId: null,
    },
    {
      address:
        "anim1qv9pzxqlyckngw6zf9g9whn9d3eh4qvg37tfmf9tk2uup37w6hwq5n0vt6",
      hrp: "anim",
      payloadHex:
        "030a11181f262d343b424950575e656c737a81888f969da4abb2b9c0c7ced5dc",
      algId: null,
    },
    {
      address:
        "anim1qv9pzxqlyckngw6zf9g9whn9d3eh4qvg37tfmf9tk2uup37w6hww86szwz0ag",
      hrp: "anim",
      payloadHex:
        "030a11181f262d343b424950575e656c737a81888f969da4abb2b9c0c7ced5dce3ea",
      algId: null,
    },
    {
      address:
        "anim1qv9pzxqlyckngw6zf9g9whn9d3eh4qvg37tfmf9tk2uup37w6hww86h3lrlsvrg5npp3ml",
      hrp: "anim",
      payloadHex:
        "030a11181f262d343b424950575e656c737a81888f969da4abb2b9c0c7ced5dce3eaf1f8ff060d14",
      algId: null,
    },
    // BIP-173: all-uppercase form is valid.
    {
      address:
        "ANIM1ZQPHEJ6VEJT4RREN3Q9RXS7GQW56ECFS4FW64SFH939VXV9X4XC7X0SQDH2UN",
      hrp: "anim",
      payloadHex:
        "10037ccb4ccc97518f33880a3343c803a9ace130aa5daac1372c4ac330a6a9b1e33e",
      algId: 4099,
      note: "all-uppercase form is valid",
    },
  ],
  invalid: [
    {
      address:
        "anim1zqphej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0sqdh2uq",
      reason: "corrupted checksum (last char)",
    },
    {
      address:
        "anim1zqphej6vejt4rren3q9rxs7gqw56qcfs4fw64sfh939vxv9x4xc7x0sqdh2un",
      reason: "corrupted data char",
    },
    {
      address:
        "anim1zqpheJ6VEJT4RREN3Q9RXS7GQW56ECFS4FW64SFH939VXV9X4XC7X0SQDH2UN",
      reason: "mixed case",
    },
    {
      address:
        "bnim1zqphej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0sqdh2un",
      reason: "wrong hrp + checksum fail",
    },
    {
      address:
        "anim1zqphej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0s438xe3",
      reason: "bech32 (not bech32m) checksum constant",
    },
    { address: "animqqqqqq", reason: "no separator" },
    {
      address:
        "anim1zqphej6vejt4rren3q9rxs7gqw56ecfs4fw64sfh939vxv9x4xc7x0sqdhb1o",
      reason: "chars outside charset",
    },
  ],
};
