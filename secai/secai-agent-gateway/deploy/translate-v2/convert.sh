#!/bin/sh
set -eu
mkdir -p /tmp/lo-profile/user
cat > /tmp/lo-profile/user/registrymodifications.xcu <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  </item>
</oor:items>
XML
exec soffice -env:UserInstallation=file:///tmp/lo-profile --headless --nologo --nodefault \
  --nofirststartwizard --convert-to docx --outdir /output /input/document.doc
