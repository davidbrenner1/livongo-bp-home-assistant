# Publish the prebuilt app

## 1. Configure your GitHub owner

From the repository root:

```bash
python3 tools/configure_for_github.py davidbrenner1
```

## 2. Create and push the GitHub repository

Create a repository named:

```text
livongo-bp-home-assistant
```

Then push this repository to `main`.

## 3. Let GitHub Actions build the image

The workflow `.github/workflows/build.yml` publishes:

```text
ghcr.io/davidbrenner1/livongo-bp-collector:0.2.0
ghcr.io/davidbrenner1/livongo-bp-collector:latest
```

as one multi-platform manifest supporting:

- linux/amd64
- linux/arm64

The workflow uses the repository-provided `GITHUB_TOKEN` with `packages: write`; no separate registry password is required.

## 4. Make the GHCR package public

In GitHub, open your profile -> Packages -> `livongo-bp-collector` -> Package settings and set visibility to Public. A public image lets Home Assistant pull it without registry credentials.

## 5. Add the repository to Home Assistant

In Home Assistant, open the Apps store and add:

```text
https://github.com/davidbrenner1/livongo-bp-home-assistant
```

Then install Livongo BP Collector. Because `config.yaml` contains an `image:` field, Supervisor pulls the `0.2.0` image rather than executing the Dockerfile locally.
