# Releasing controlplane-events

The standalone Events container serves the HTTP inbox. Main qualification builds the image from the verified wheel and advances the moving `:test` tag. Production uses a versioned OCI image.

The `0.1` runtime tag admission verifies the package version and candidate wheel built from the tag source. The stable publisher checks that admission and the exact main publication run. It checks the selected manifest digest, source revision, version, and wheel digest before copying the qualified image to the version tag. The stable operation leaves the existing package release records and moving TEST channel in place.

Public container publication and the remaining Apache repositories are tracked in [FyberLabs/infra#993](https://github.com/FyberLabs/infra/issues/993).
