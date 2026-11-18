# Publishing this repository on GitHub

The directory containing this file is part of the **layer-library** repository.
Publish each project as its own repository.

## Upload the source

1. Extract the source ZIP and open a terminal in the `layer-library` directory that
   contains `README.md`, `Dockerfile` and `compose.yaml`.
2. Create an empty repository named `layer-library` on GitHub. Choose **Public** if
   you want others to browse and use it. Leave the GitHub options to generate a
   README, licence and ignore file unchecked; they are already included here.
3. Run the following commands, replacing `YOUR-USERNAME` with your GitHub user
   or organisation name:

```sh
git init -b main
git add .
git commit -m "Initial public release"
git remote add origin https://github.com/YOUR-USERNAME/layer-library.git
git push -u origin main
```

Git must be installed and authenticated to GitHub. GitHub Desktop can also
publish an existing local repository if you prefer a graphical application.

Upload the extracted source, including the dotfiles and `.github` directory,
so people can browse, clone and contribute to the project. You can additionally
attach the original source ZIP to a GitHub Release.

Official GitHub guide:
https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github

## After the first push

- Check the **Actions** tab. The included workflow tests the application, builds
  its container and waits for the container health check.
- Review the README and repository description.
- Enable private vulnerability reporting in the repository settings if you
  intend to offer that reporting channel.
- Tag a release after checking the workflow result. The package includes an MIT
  licence and the required bundled third-party notices.

The included Compose setup builds an image from source locally. It does not
depend on an unpublished container image or a particular GitHub account. Users
can follow the README after cloning or downloading the repository.

## What is included

The repository contains application source, required static assets, installation
examples and tests. It does not contain a personal installation's live data or
configuration. Keep those outside version control.

Both this project and the other companion application can run independently.
See the README for the optional connection between Form Studio and Layer
Library.

