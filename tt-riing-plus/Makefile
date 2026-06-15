# ─────────────────────────────────────────────
# TT Riing Plus Control — Packaging Makefile
# ─────────────────────────────────────────────
# Targets:
#   make install     — Run install.sh
#   make uninstall   — Run uninstall.sh
#   make desktop     — Install .desktop to system
#   make deb         — Build .deb package (requires dpkg-deb)
#   make clean       — Remove build artifacts
# ─────────────────────────────────────────────

PREFIX ?= /opt/tt-riing-plus
BINDIR ?= $(PREFIX)
DESKTOPDIR ?= /usr/share/applications
SYSTEMDDIR ?= /usr/lib/systemd/user

.PHONY: install uninstall desktop deb clean

install:
	bash install.sh

uninstall:
	bash uninstall.sh

# Install .desktop system-wide (requires sudo)
desktop:
	sudo cp tt-riing-plus.desktop "$(DESKTOPDIR)/tt-riing-plus.desktop"
	sudo sed -i "s|{INSTALL_DIR}|$(PREFIX)|g" "$(DESKTOPDIR)/tt-riing-plus.desktop"
	sudo update-desktop-database "$(DESKTOPDIR)" 2>/dev/null || true
	@echo "✅ .desktop installed to $(DESKTOPDIR)"

# Build .deb package structure
# Usage: make deb PREFIX=/opt/tt-riing-plus
deb: clean
	@echo "📦 Building .deb package..."
	@echo ""
	@# Check for dpkg-deb
	@which dpkg-deb >/dev/null 2>&1 || (echo "❌ dpkg-deb not found. Install with: sudo apt install dpkg" && exit 1)
	@
	@# Build staging directory
	@rm -rf deb-staging
	@mkdir -p "$(PREFIX)" deb-staging/DEBIAN
	@cp -r tt_riing_plus.py tt_features.py tt-riing-plus.sh install.sh uninstall.sh requirements.txt README.md "$(PREFIX)"/
	@
	@# Fix .desktop
	@mkdir -p "$(DESKTOPDIR)"
	@sed "s|{INSTALL_DIR}|$(PREFIX)|g" tt-riing-plus.desktop > "$(DESKTOPDIR)/tt-riing-plus.desktop"
	@
	@# Fix systemd
	@mkdir -p "$(SYSTEMDDIR)"
	@sed "s|{INSTALL_DIR}|$(PREFIX)|g" tt-riing-plus.service.in > "$(SYSTEMDDIR)/tt-riing-plus.service" 2>/dev/null || true
	@
	@# Control file
	@cat > deb-staging/DEBIAN/control << 'CONTROL'
Package: tt-riing-plus
Version: 2.1.0
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-venv, python3-pip
Maintainer: Bjk201 <bjk201@github.com>
Description: Thermaltake Riing Plus Fan & RGB Control
 Linux control software for Thermaltake Riing Plus, Trio, Quad,
 and compatible USB HID controllers. Features PWM fan control,
 RGB lighting effects, temperature-based auto mode, profiles,
 and per-channel configuration.
CONTROL
	@
	@# Build
	@dpkg-deb --build deb-staging tt-riing-plus_2.1.0_all.deb 2>/dev/null
	@echo ""
	@echo "✅ Package built: tt-riing-plus_2.1.0_all.deb"
	@echo "   Install with: sudo dpkg -i tt-riing-plus_2.1.0_all.deb"

clean:
	rm -rf deb-staging *.deb __pycache__ .venv
	@echo "✅ Cleaned"
