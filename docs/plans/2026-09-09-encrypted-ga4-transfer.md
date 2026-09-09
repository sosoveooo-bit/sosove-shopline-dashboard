# Encrypted GA4 Transfer

## Explicit Scope

The owner approved publishing an encrypted GA4 credential bundle to this public repository and receiving its decryption code separately. The plaintext credential and decryption code must never be committed, added to Docker images, or provided to GitHub Actions. No connection to the NAS is made by this change; the owner executes the import command there.

## Design

- Use the established `cryptography.fernet` authenticated-encryption recipe, with a freshly generated random key (not a human password). Publish only the Fernet ciphertext. Resource settings and service-account metadata are inside the encrypted payload.
- Generate the transfer code locally beside the owner's original private file, outside the Git checkout. Verify round-trip equality before publishing. Keep the code out of shell arguments, environment variables, public docs and automated logs.
- NAS imports use the existing local dashboard image, addressed by image ID, as a temporary decryption helper. It has no network or mounts, a read-only filesystem and Docker logging explicitly disabled. Input/output pipes carry data in memory, not files or command arguments.
- Verify ciphertext authenticity and payload shape before any writes. Save the decoded key in `secrets/ga.json` as root:10001 mode 0640, set directory 0750, update only GA4 fields in `.env`, preserve backups and restart only the dashboard container. Verify health and key readability; GA4 API authorization is checked from the dashboard afterward.
- Only synthetic credentials are used in CI, including the wrong-code test. The genuine published ciphertext is never decrypted in CI.

## References

- [Fernet authenticated encryption](https://cryptography.io/en/latest/fernet/)
- Removing ciphertext from the working tree does not remove Git history. Keep the transfer code private; if either the transfer code or the GA4 private key leaks, revoke/rotate the service-account key in Google Cloud.
