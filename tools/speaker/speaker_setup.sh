#!/bin/bash
# One-time setup for the MAX98357A speaker amp on the Raspberry Pi 5.
# Run on the Pi:   bash speaker_setup.sh
#
# Wiring (Pi powered OFF while wiring):
#   Vin  -> pin 2  (5V)      GND -> pin 6  (GND)
#   BCLK -> pin 12 (GPIO 18) LRC -> pin 35 (GPIO 19)   DIN -> pin 40 (GPIO 21)
#   Speaker red -> amp +, black -> amp -

set -e
CONFIG=/boot/firmware/config.txt

echo "== Installing tools (espeak-ng for the robot voice, alsa-utils for aplay) =="
sudo apt-get update -qq
sudo apt-get install -y espeak-ng alsa-utils

echo "== Enabling the MAX98357A in $CONFIG =="
if grep -q "^dtoverlay=max98357a" "$CONFIG"; then
  echo "Already enabled."
  echo "Done. Now run:  python3 speaker_test.py"
else
  sudo cp "$CONFIG" "$CONFIG.bak"          # backup, just in case
  echo "dtoverlay=max98357a" | sudo tee -a "$CONFIG" > /dev/null
  echo "Added. A backup of the old file is at $CONFIG.bak"
  echo
  echo "Reboot needed. After it restarts, run:  python3 speaker_test.py"
  read -p "Reboot now? [y/N] " ans
  if [[ "$ans" == "y" || "$ans" == "Y" ]]; then sudo reboot; fi
fi
