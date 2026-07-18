# dawft — the face packer (third-party, GPL)

The watch-face builders in [`../solar-soc/`](../solar-soc/) call **`dawft`** ("Da Watch Face
Tool") to assemble the final MoYoung / Da Fit `.bin` from a folder of `.bmp` blobs plus a
`watchface.txt` layout. It parses and creates the same `.bin` format the watch's stock firmware
loads.

`dawft` is a **separate, third-party tool** by David Atkinson, licensed **GPL-2.0-or-later**:
<https://github.com/david47k/dawft>. It is deliberately **not vendored** into this repository —
this project is AGPLv3-licensed and the GPL-2.0 dawft stays at arm's length (the builders shell out to
it as a subprocess; they don't link or vendor it).

## Get it

```bash
./get-dawft.sh          # clones david47k/dawft into ./src and builds ./dawft
```

The builders resolve the tool in this order:

1. `$DAWFT` environment variable (absolute path to a `dawft` binary), then
2. `dawft` on your `$PATH`, then
3. `./dawft` here (what `get-dawft.sh` produces).

`./src/` and the built `./dawft` binary are git-ignored.
