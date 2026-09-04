# Real World Testing Enanchment on [nemo@192.168.1.105]:

1. The qt6_gui drawer must be "almost" full screen cards. they must have a solid background and must cover the whole screen except a small margin (e.g 30px) on all sides and the command bar.
2. We must create a Phone Widget. it must show the recents calls , favorites and contacts. when we receive a call it must show the call widget on clock page and must allow us to answer the call, reject the call or mute the call. we have to handle hfp in our application and we havo to handle every available hfp commands/information to make the phone widget work as expected. we have to try to route hfp audio through our app.
   - interesting logs:

   ```
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: 17:15:25 | INFO     | channel_manager.phone_status - 📞 PhoneStatus raw wire bytes (1062B): 0aa108080110001a102b33392033343920333332203235393122114c6f726564616e61204469204c696c6c6f2a0943656c6c756c61726532ea0789504e470d0a1a0a0000000d49484452000001000000010008060000005c72a8660000000473424954080808087c086488000000017352474200aece1ce90000039449444154789cedd72d4ac4410087e1ffbaeb177b0ebb78058388c168113c82c9ee09c42278132f6559448345ab9b16838cfa3e4f1ff8c1c0cbcc6c797bfc3101495ba30700e30800840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900842d460f60b3aba3d3e9feecfa5b679e5f57d3c1ddc50f2de2bf10803f603e9b4fdb5bdfbbaaedb9ab65335f00081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300081300085b8c1ec0cf58eeec4d8fe737a367ac79797f9b6e9e1e46cfe00b01f8a7f617bbd3e5e1c9e8196b9edf5602f0cbf80240980040980040980040980040980040980040980040980040980040980040d86c797bfc317a0430861700840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900840900847d02f6901753abb630730000000049454e44ae4260821000
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: 17:15:25 | INFO     | channel_manager.phone_status - 📞 PhoneStatus parsed proto:
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: calls {
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   call_state: IN_CALL
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   call_duration_seconds: 0
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   phone_number: "+39 349 332 2591"
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   display_name: "Loredana Di Lillo"
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   contact_id: "Cellulare"
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]:   contact_photo: "\211PNG\r\n\032\n\000\000\000\rIHDR\000\000\001\000\000\000\001\000\010\006\000\000\000\\r\250f\000\000\000\004sBIT\010\010\010\010|\010d\210\000\000\000\001sRGB\000\256\316\034\351\000\000\003\224IDATx\234\355\327-J\304A\000\207\341\377\272\353\027{\016\273x\005\203\210\301h\021<\202\311\356\t\304\"x\023/eYD\203E\253\233\026\203\214\372>O\037\370\301\300\313\314ly{\3741\001I[\243\007\000\343\010\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204-F\017`\263\253\243\323\351\376\354\372[g\236_W\323\301\335\305\017-\342\277\020\200?`>\233O\333[\337\273\252\355\271\253e3_\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010\023\000\010[\214\036\300\317X\356\354M\217\3477\243g\254yy\177\233n\236\036F\317\340\013\001\370\247\366\027\273\323\345\341\311\350\031k\236\337V\002\360\313\370\002@\230\000@\230\000@\230\000@\230\000@\230\000@\230\000@\230\000@\230\000@\230\000@\230\000@\330ly{\3741z\0040\206\027\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204\t\000\204}\002\366\220\027S\253\2660s\000\000\000\000IEND\256B`\202"
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: }
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: signal_strength: 0
   set 04 17:15:25 NemoKarr-PC nemo-kiosk[672]: 17:15:25 | INFO     | channel_manager.phone_status - 📞 PhoneStatusUpdate: state=ACTIVE, caller='Loredana Di Lillo', signal=0/5
   ```

3. We must analyze Navigation Widget of Android Automotive Head Unit (AAHU) and we have to implement it in our application. current implementation doesn't work as expected wrong icons, wrong distances to next turn, only thing working is next road name. we must use third_party info to fully understand the proto.
   - interesting logs:
   ```
   set 04 17:15:30 NemoKarr-PC nemo-kiosk[672]: 17:15:30 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:30 NemoKarr-PC nemo-kiosk[672]: 17:15:30 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   set 04 17:15:32 NemoKarr-PC nemo-kiosk[672]: 17:15:32 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:32 NemoKarr-PC nemo-kiosk[672]: 17:15:32 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   set 04 17:15:33 NemoKarr-PC nemo-kiosk[672]: 17:15:33 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:33 NemoKarr-PC nemo-kiosk[672]: 17:15:33 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   set 04 17:15:35 NemoKarr-PC nemo-kiosk[672]: 17:15:35 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:35 NemoKarr-PC nemo-kiosk[672]: 17:15:35 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   set 04 17:15:37 NemoKarr-PC nemo-kiosk[672]: 17:15:37 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:37 NemoKarr-PC nemo-kiosk[672]: 17:15:37 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   set 04 17:15:39 NemoKarr-PC nemo-kiosk[672]: 17:15:39 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=2, side=4
   set 04 17:15:39 NemoKarr-PC nemo-kiosk[672]: 17:15:39 | INFO     | channel_manager - 🧭 [Navigation Channel] Turn step: road='Via Giulio e Corrado Venini', dist=0.0m, maneuver=553, side=1200
   ```
4. During Real World tests, AA video disapeared and after rebooting the system it came back (i've tried Video Focus Toggle, but it didn't help) [logs in journalctl around set 04 17:06:43]. we need to investigate the root cause.
5. During Real World tests, often Bluetooth wasn't recognized. [logs in journalctl around set 04 16:52:41] we have to investigate the root cause.
6. During Real World tests, after the system disconnect from the USB audio the audio output was not routed to the internal audio and when USB audio came back it doesn't recover [logs in journalctl starting at set 04 17:32:58 ending at set 04 17:33:12]. we need to investigate the root cause.
   - interesting logs:
   ```
   set 04 17:32:58 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 2
   set 04 17:32:58 NemoKarr-PC wireplumber[586]: spa.alsa: 0x5616c940e818: poll fd error/hangup (card removed?), removing poll sources
   set 04 17:32:58 NemoKarr-PC nemo-kiosk[678]: 17:32:58 | INFO     | qt6_gui.audio_handler - 🔊 [Audio Ch4] QAudioSink state: State.StoppedState (error=Error.IOError)
   set 04 17:32:58 NemoKarr-PC nemo-kiosk[678]: 17:32:58 | INFO     | qt6_gui.audio_handler - 🔊 [Audio Ch5] QAudioSink state: State.StoppedState (error=Error.IOError)
   set 04 17:32:58 NemoKarr-PC kernel: usb 1-2.2: USB disconnect, device number 3
   set 04 17:32:58 NemoKarr-PC pipewire[585]: spa.alsa: front:1p: snd_pcm_drop: Nessun device corrisponde
   set 04 17:32:58 NemoKarr-PC pipewire[585]: spa.alsa: front:1p: close failed: Nessun device corrisponde
   set 04 17:32:58 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 4 using xhci_hcd
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:32:59 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:32:59 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2.2: new full-speed USB device number 5 using xhci_hcd
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2.2: New USB device found, idVendor=08bb, idProduct=2704, bcdDevice= 1.00
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2.2: New USB device strings: Mfr=1, Product=2, SerialNumber=0
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2.2: Product: USB Audio DAC
   set 04 17:32:59 NemoKarr-PC kernel: usb 1-2.2: Manufacturer: Burr-Brown from TI
   set 04 17:32:59 NemoKarr-PC kernel: input: Burr-Brown from TI               USB Audio DAC    as /devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2.2/1-2.2:1.2/0003:08BB:2704.0004/input/input16
   set 04 17:32:59 NemoKarr-PC kernel: hid-generic 0003:08BB:2704.0004: input,hidraw0: USB HID v1.00 Device [Burr-Brown from TI               USB Audio DAC   ] on usb-0000:00:14.0-2.2/input2
   set 04 17:32:59 NemoKarr-PC systemd[538]: Reached target Sound Card.
   set 04 17:33:04 NemoKarr-PC wireplumber[586]: spa.alsa: 0x5616c9610ef8: poll fd error/hangup (card removed?), removing poll sources
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 4
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2.2: USB disconnect, device number 5
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 6 using xhci_hcd
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:04 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:04 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:04 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 6
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 7 using xhci_hcd
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: device descriptor read/64, error -71
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:05 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:05 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:05 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 7
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 8 using xhci_hcd
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:06 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:06 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 8
   set 04 17:33:06 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 9 using xhci_hcd
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:07 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:07 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2.2: new full-speed USB device number 10 using xhci_hcd
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2.2: New USB device found, idVendor=08bb, idProduct=2704, bcdDevice= 1.00
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2.2: New USB device strings: Mfr=1, Product=2, SerialNumber=0
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2.2: Product: USB Audio DAC
   set 04 17:33:07 NemoKarr-PC kernel: usb 1-2.2: Manufacturer: Burr-Brown from TI
   set 04 17:33:07 NemoKarr-PC kernel: input: Burr-Brown from TI               USB Audio DAC    as /devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2.2/1-2.2:1.2/0003:08BB:2704.0005/input/input17
   set 04 17:33:07 NemoKarr-PC kernel: hid-generic 0003:08BB:2704.0005: input,hidraw0: USB HID v1.00 Device [Burr-Brown from TI               USB Audio DAC   ] on usb-0000:00:14.0-2.2/input2
   set 04 17:33:08 NemoKarr-PC wireplumber[586]: spa.alsa: 0x5616c9610ef8: poll fd error/hangup (card removed?), removing poll sources
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 9
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2.2: USB disconnect, device number 10
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 11 using xhci_hcd
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:08 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:08 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:08 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:09 NemoKarr-PC kernel: usb 1-2.2: new full-speed USB device number 12 using xhci_hcd
   set 04 17:33:09 NemoKarr-PC kernel: usb 1-2.2: New USB device found, idVendor=08bb, idProduct=2704, bcdDevice= 1.00
   set 04 17:33:09 NemoKarr-PC kernel: usb 1-2.2: New USB device strings: Mfr=1, Product=2, SerialNumber=0
   set 04 17:33:09 NemoKarr-PC kernel: usb 1-2.2: Product: USB Audio DAC
   set 04 17:33:09 NemoKarr-PC kernel: usb 1-2.2: Manufacturer: Burr-Brown from TI
   set 04 17:33:09 NemoKarr-PC kernel: input: Burr-Brown from TI               USB Audio DAC    as /devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2.2/1-2.2:1.2/0003:08BB:2704.0006/input/input18
   set 04 17:33:09 NemoKarr-PC kernel: hid-generic 0003:08BB:2704.0006: input,hidraw0: USB HID v1.00 Device [Burr-Brown from TI               USB Audio DAC   ] on usb-0000:00:14.0-2.2/input2
   set 04 17:33:10 NemoKarr-PC wireplumber[586]: spa.alsa: 0x5616c9610ef8: poll fd error/hangup (card removed?), removing poll sources
   set 04 17:33:10 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 11
   set 04 17:33:10 NemoKarr-PC kernel: usb 1-2.2: USB disconnect, device number 12
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 13 using xhci_hcd
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:11 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:11 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: USB disconnect, device number 13
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: new high-speed USB device number 14 using xhci_hcd
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: New USB device found, idVendor=214b, idProduct=7260, bcdDevice= 1.00
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: New USB device strings: Mfr=0, Product=1, SerialNumber=0
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2: Product: USB2.0 HUB
   set 04 17:33:11 NemoKarr-PC kernel: hub 1-2:1.0: USB hub found
   set 04 17:33:11 NemoKarr-PC kernel: hub 1-2:1.0: 4 ports detected
   set 04 17:33:11 NemoKarr-PC kernel: usb 1-2.2: new full-speed USB device number 15 using xhci_hcd
   set 04 17:33:12 NemoKarr-PC kernel: usb 1-2.2: New USB device found, idVendor=08bb, idProduct=2704, bcdDevice= 1.00
   set 04 17:33:12 NemoKarr-PC kernel: usb 1-2.2: New USB device strings: Mfr=1, Product=2, SerialNumber=0
   set 04 17:33:12 NemoKarr-PC kernel: usb 1-2.2: Product: USB Audio DAC
   set 04 17:33:12 NemoKarr-PC kernel: usb 1-2.2: Manufacturer: Burr-Brown from TI
   set 04 17:33:12 NemoKarr-PC kernel: input: Burr-Brown from TI               USB Audio DAC    as /devices/pci0000:00/0000:00:14.0/usb1/1-2/1-2.2/1-2.2:1.2/0003:08BB:2704.0007/input/input19
   set 04 17:33:12 NemoKarr-PC kernel: hid-generic 0003:08BB:2704.0007: input,hidraw0: USB HID v1.00 Device [Burr-Brown from TI               USB Audio DAC   ] on usb-0000:00:14.0-2.2/input2
   set 04 17:33:30 NemoKarr-PC nemo-kiosk[678]: 17:33:30 | INFO     | qt6_gui.audio_handler - 🔊 [Audio Ch5] Prebuffer FILLED (6144B / 150ms) — starting push playback
   ```
7. We have to bind BT device Phiscal volume button to our application.
8. We have to log hfp protocol data
9. We have to avoid UNDERRUN badge when we have "paused" the Audio ch. (we know it thanks to audio focus messages)
