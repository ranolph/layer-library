# Third-party notices

Layer Library's original application code and included project assets are
licensed under the [MIT License](LICENSE). Third-party components retain their
own copyright notices and licenses.

## Browser viewer and build tool

| Component | Version | Use | License |
| --- | --- | --- | --- |
| [Three.js](https://github.com/mrdoob/three.js/tree/r187) | 0.187.0 (r187) | Rendering, controls, and model loaders included in `app/static/viewer.bundle.js` | [MIT; full upstream notice](licenses/three-LICENSE.txt) |
| [fflate](https://github.com/101arrowz/fflate/tree/v0.8.3) | 0.8.3 | Decompression library supplied with Three.js and included in `app/static/viewer.bundle.js` | [MIT; full upstream notice](licenses/fflate-LICENSE.txt) |
| [esbuild](https://github.com/evanw/esbuild/tree/v0.28.2) | 0.28.2 | Development tool used to build the browser viewer; not an application runtime dependency | [MIT; full upstream notice](licenses/esbuild-LICENSE.txt) |

Three.js and esbuild versions are pinned in `package-lock.json`. The fflate
version is recorded in the bundled Three.js library's license comment; it is
included through Three.js rather than as a separate direct npm dependency.

The prebuilt viewer retains its upstream copyright and license comments.
`npm run build` uses `--legal-comments=eof` to preserve them when rebuilding.
Keep those comments and the full license files when redistributing the viewer.

## Python and container dependencies

Python runtime dependencies are listed in `requirements.txt`; development
dependencies are listed in `requirements-dev.txt`. They are installed from their
package distributions rather than vendored as source in this repository. Those
distributions retain their respective copyright and license notices. The
application's MIT License does not replace the licenses of Python, its packages,
or software supplied by the container base image.

## Project assets and integrations

The included SVG icon is a project asset covered by the root MIT License. The
repository does not ship a personal model collection, user photographs, or
printer credentials. Models, photographs, and other files uploaded by a user
keep their own ownership and license terms.

Printer transfer is implemented in the application using Python's standard
library. The repository does not include Bambu Lab's application software or the
third-party printer integrations referenced in its setup documentation. Product
and service names identify supported formats or integration targets; their
mention does not imply affiliation or endorsement.
