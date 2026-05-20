<?php
# Synthetic LocalSettings excerpt — NEVER commit a real one with credentials.

$wgSitename = "Demo Wiki";
$wgServer = "https://wiki.demo.local";
$wgScriptPath = "/w";
$wgArticlePath = "/wiki/$1";
$wgUsePathInfo = true;

# DB settings (left blank on purpose — we don't read them).
$wgDBserver   = "";
$wgDBname     = "";
$wgDBuser     = "";
$wgDBpassword = "";
