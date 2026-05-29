[app]
title = 全能朗读器
package.name = myreader
package.domain = org.myreader
source.dir = .
version = 1.0

requirements = python3,kivy==2.2.1,pyjnius==1.4.1,android,PyPDF2,python-docx,ebooklib,zhconv,mobi

android.permissions = READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, RECORD_AUDIO, FOREGROUND_SERVICE, WAKE_LOCK

android.api = 30
android.minapi = 24
android.ndk = 23b
android.sdk = 30
android.build_tools = 30.0.3
android.use_aapt2 = True

log_level = 2
p4a.branch = master
