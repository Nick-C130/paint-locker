# Changelog

All notable changes to Paint Locker. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
* "Buy next" purchase recommender: ranks paints you don't own by how much new colour they let you
  mix and by your recent colour requests, with an explanation for each pick.
* "Find in racks" from the mix lab and saved mixes: shows the racks with the needed bottles highlighted.
* Swatch borders by paint type everywhere a rack is shown (acrylic, airbrush, thinner/medium, wash,
  metallic, primer, mix batch), with a legend.
* `docker-compose.deploy.yml` for running the published image with no source checkout.
* MIT licence, contributing guide, issue and pull request templates, ruff lint in CI.

### Fixed
* Paints such as "Medium Green" and "Cement Grey" were imported as mediums.

## [1.0.0] - 2026-11

### Added
* Paint catalogue (4,200+ paints), storage racks, bottle formats, barcode scanning, mix lab with
  spectral mixing and reverse-mix suggestions, water/thinner in mixes, mix batches, WW2 reference
  colours, colour request log, comments and photos, dark mode, nightly backups.
