pkgname=mouse-control
pkgver=0.8.2
pkgrel=1
pkgdesc='Headless evdev/uinput mouse remapping with optional hardware backends'
arch=('any')
url='https://github.com/DonGeronimo7/mouse-control'
license=('GPL-3.0-or-later')
depends=('python' 'python-evdev' 'python-dbus-next' 'python-packaging' 'systemd')
makedepends=('git' 'python-build' 'python-installer' 'python-setuptools')
optdepends=('openrazer: optional Razer hardware integration')
# Stable source is pinned to the exact upstream release tag.
source=("mouse-control::git+https://github.com/DonGeronimo7/mouse-control.git#tag=v${pkgver}")
sha256sums=('SKIP')

build() {
  cd "$srcdir/mouse-control"
  python -m build --wheel --no-isolation --outdir dist
}

package() {
  cd "$srcdir/mouse-control"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 src/mouse_control/udev/71-mouse-control-uaccess.rules \
    "$pkgdir/usr/lib/udev/rules.d/71-mouse-control-uaccess.rules"
  install -Dm644 packaging/appimage/mouse-control.desktop \
    "$pkgdir/usr/share/applications/mouse-control.desktop"
  for size in 512 256 128 64 48 32; do
    install -Dm644 "assets/icons/hicolor/${size}x${size}/apps/mouse-control.png" \
      "$pkgdir/usr/share/icons/hicolor/${size}x${size}/apps/mouse-control.png"
  done
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
