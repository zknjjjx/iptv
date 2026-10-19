include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-iptv-speedtest
PKG_VERSION:=1.0.0
PKG_RELEASE:=3
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-iptv-speedtest
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=IPTV speedtest (native, no Docker)
  URL:=https://github.com/zknjjjx/iptv
  DEPENDS:=+python3-light +python3-urllib +python3-openssl +python3-logging +python3-codecs +luci-base +rpcd-mod-file
endef

define Package/luci-app-iptv-speedtest/description
 Native IPTV speedtest with procd supervision, UCI settings and LuCI entry.
 Repository has no declared license; this package does not add a license grant.
endef

define Package/luci-app-iptv-speedtest/conffiles
/etc/config/iptv_speedtest
/etc/iptv-speedtest/
endef

define Build/Compile
endef

define Package/luci-app-iptv-speedtest/install
	$(CP) $(CURDIR)/files/* $(1)/
	$(INSTALL_DIR) $(1)/usr/share/iptv-speedtest
	$(INSTALL_DATA) $(CURDIR)/../app.py $(1)/usr/share/iptv-speedtest/app.py
	chmod 755 $(1)/etc/init.d/iptv-speedtest
	chmod 755 $(1)/usr/libexec/iptv-speedtest-ctl
	chmod 600 $(1)/etc/config/iptv_speedtest
endef

define Package/luci-app-iptv-speedtest/postinst
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
umask 077
mkdir -p /etc/iptv-speedtest
chmod 700 /etc/iptv-speedtest
chmod 600 /etc/config/iptv_speedtest
exit 0
endef

define Package/luci-app-iptv-speedtest/prerm
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
/etc/init.d/iptv-speedtest stop
/etc/init.d/iptv-speedtest disable
exit 0
endef

$(eval $(call BuildPackage,luci-app-iptv-speedtest))
