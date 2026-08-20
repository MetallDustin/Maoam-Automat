/*
  MAOM-Automat – Arduino Mega Hardware-Treiber

  Raspberry Pi <-> Mega: 9600 Baud, eine Textzeile pro Nachricht.
  Pi -> Mega: RELAY ON|OFF, LED <1-5> ON|OFF, LEDS OFF,
              LCD <Zeile 1>|<Zeile 2>, DISPENSE <1-5>, PING
  Mega -> Pi: COIN <5|10|20>, BUTTON <1-5>, KEY ON|OFF, OK <1-5>, PONG

  Alle Tasten und der Schluesselschalter liegen gegen GND und verwenden
  INPUT_PULLUP. Den Muenzpruefer bei Bedarf mit COIN_ACTIVE_LOW anpassen.
*/
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Servo.h>

const byte SERVO_PINS[5] = {2, 3, 4, 5, 6};
const byte BUTTON_PINS[5] = {A1, A2, A3, A4, A5};
const byte LED_PINS[5] = {A6, A7, A8, A9, A10};
const byte COIN_PIN = A0;
const byte KEY_PIN = A11;
const byte RELAY_PIN = A12;

const bool COIN_ACTIVE_LOW = true;
const bool RELAY_ACTIVE_HIGH = true;  // auf false setzen, falls umgekehrt
const unsigned long BUTTON_DEBOUNCE_MS = 35;
const unsigned long COIN_GAP_MS = 350;
const unsigned long STARTUP_CHASE_MS = 600;
const unsigned long STARTUP_BLINK_MS = 700;

// Diese Winkel beim mechanischen Test fuer jedes Fach kalibrieren.
int homeAngle[5] = {0, 0, 0, 0, 0};
int dispenseAngle[5] = {90, 90, 90, 90, 90};
unsigned int servoOutMs[5] = {450, 450, 450, 450, 450};
unsigned int servoBackMs[5] = {450, 450, 450, 450, 450};

LiquidCrystal_I2C lcd(0x27, 16, 2); // haeufige Adresse: 0x27, sonst 0x3F
Servo servos[5];
bool buttonRaw[5], buttonStable[5];
unsigned long buttonChangedAt[5];
bool lastCoinLevel;
byte coinPulses = 0;
unsigned long lastCoinPulseAt = 0;
bool lastKeyState;
bool hardwareBusy = false;
char command[96];
byte commandLength = 0;
enum StartupPhase { STARTUP_CHASE, STARTUP_BLINK, STARTUP_DONE };
StartupPhase startupPhase = STARTUP_CHASE;
byte startupStep = 0;
bool startupBlinkOn = false;
unsigned long startupChangedAt = 0;

void sendLine(const char *text) {
  Serial.println(text);
}

void setRelay(bool enabled) {
  digitalWrite(RELAY_PIN, (enabled == RELAY_ACTIVE_HIGH) ? HIGH : LOW);
}

void setLed(byte index, bool enabled) {
  if (index < 5) digitalWrite(LED_PINS[index], enabled ? HIGH : LOW);
}

void printLcdLine(const char *text) {
  // Genau 16 Zeichen schreiben: Rest loeschen, Ueberlaenge nicht umbrechen.
  bool endReached = false;
  for (byte i = 0; i < 16; i++) {
    char character = endReached ? ' ' : text[i];
    if (character == '\0') { endReached = true; character = ' '; }
    lcd.print(character);
  }
}

void showLcd(const char *line1, const char *line2) {
  lcd.setCursor(0, 0);
  printLcdLine(line1);
  lcd.setCursor(0, 1);
  printLcdLine(line2);
}

void setAllLeds(bool enabled) {
  for (byte i = 0; i < 5; i++) setLed(i, enabled);
}

void updateStartupAnimation() {
  if (startupPhase == STARTUP_DONE) return;
  unsigned long now = millis();

  if (startupPhase == STARTUP_CHASE && now - startupChangedAt >= STARTUP_CHASE_MS) {
    setLed(startupStep % 5, false);
    startupStep++;
    startupChangedAt = now;
    if (startupStep >= 10) { // zwei langsame Durchlaeufe
      startupPhase = STARTUP_BLINK;
      startupBlinkOn = true;
      setAllLeds(true);
    } else {
      setLed(startupStep % 5, true);
    }
  } else if (startupPhase == STARTUP_BLINK && now - startupChangedAt >= STARTUP_BLINK_MS) {
    startupBlinkOn = !startupBlinkOn;
    setAllLeds(startupBlinkOn);
    startupChangedAt = now;
  }
}

void dispense(byte index) {
  if (index >= 5) return;
  hardwareBusy = true;
  // Es wird immer nur dieser eine Servo angehaengt und danach getrennt.
  servos[index].attach(SERVO_PINS[index]);
  servos[index].write(homeAngle[index]);
  delay(180);
  servos[index].write(dispenseAngle[index]);
  delay(servoOutMs[index]);
  servos[index].write(homeAngle[index]);
  delay(servoBackMs[index]);
  servos[index].detach();
  // Tasten, die waehrend der Ausgabe gehalten wurden, nicht als neue Auswahl werten.
  for (byte i = 0; i < 5; i++) {
    buttonRaw[i] = buttonStable[i] = digitalRead(BUTTON_PINS[i]);
    buttonChangedAt[i] = millis();
  }
  hardwareBusy = false;
  char result[12];
  snprintf(result, sizeof(result), "OK %u", index + 1);
  sendLine(result);
}

void processCommand(char *line) {
  if (strcmp(line, "STARTUP DONE") == 0) {
    startupPhase = STARTUP_DONE;
  } else if (strcmp(line, "RELAY ON") == 0) setRelay(true);
  else if (strcmp(line, "RELAY OFF") == 0) setRelay(false);
  else if (strcmp(line, "LEDS OFF") == 0) {
    for (byte i = 0; i < 5; i++) setLed(i, false);
  } else if (strncmp(line, "LED ", 4) == 0) {
    int channel = 0;
    char state[8] = {0};
    if (sscanf(line + 4, "%d %7s", &channel, state) == 2 && channel >= 1 && channel <= 5)
      setLed(channel - 1, strcmp(state, "ON") == 0);
  } else if (strncmp(line, "DISPENSE ", 9) == 0) {
    int channel = atoi(line + 9);
    if (channel >= 1 && channel <= 5) dispense(channel - 1);
  } else if (strncmp(line, "SERVO ", 6) == 0) {
    // SERVO <1-5> <Startwinkel> <Ausgabewinkel> <Hin-ms> <Zurueck-ms>
    int channel, home, output, outMs, backMs;
    if (sscanf(line + 6, "%d %d %d %d %d", &channel, &home, &output, &outMs, &backMs) == 5 &&
        channel >= 1 && channel <= 5 && home >= 0 && home <= 180 && output >= 0 && output <= 180 &&
        outMs >= 50 && outMs <= 5000 && backMs >= 50 && backMs <= 5000) {
      byte index = channel - 1;
      homeAngle[index] = home;
      dispenseAngle[index] = output;
      servoOutMs[index] = outMs;
      servoBackMs[index] = backMs;
      char result[16]; snprintf(result, sizeof(result), "SERVO OK %u", channel); sendLine(result);
    }
  } else if (strncmp(line, "LCD ", 4) == 0) {
    char *separator = strchr(line + 4, '|');
    if (separator) {
      *separator = '\0';
      showLcd(line + 4, separator + 1);
    } else showLcd(line + 4, "");
  } else if (strcmp(line, "DISPLAYCLR") == 0) {
    showLcd("", "");
  } else if (strncmp(line, "DISPLAY1 ", 9) == 0) {
    lcd.setCursor(0, 0); printLcdLine(line + 9);
  } else if (strncmp(line, "DISPLAY2 ", 9) == 0) {
    lcd.setCursor(0, 1); printLcdLine(line + 9);
  } else if (strcmp(line, "PING") == 0) sendLine("PONG");
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      command[commandLength] = '\0';
      if (commandLength) processCommand(command);
      commandLength = 0;
    } else if (commandLength < sizeof(command) - 1) command[commandLength++] = c;
  }
}

void checkButtons() {
  if (hardwareBusy) return;
  for (byte i = 0; i < 5; i++) {
    bool now = digitalRead(BUTTON_PINS[i]);
    if (now != buttonRaw[i]) { buttonRaw[i] = now; buttonChangedAt[i] = millis(); }
    if (now != buttonStable[i] && millis() - buttonChangedAt[i] >= BUTTON_DEBOUNCE_MS) {
      buttonStable[i] = now;
      if (buttonStable[i] == LOW) {
        char event[14]; snprintf(event, sizeof(event), "BUTTON %u", i + 1); sendLine(event);
      }
    }
  }
}

void checkCoin() {
  if (hardwareBusy) return;
  bool now = digitalRead(COIN_PIN);
  const bool pulseStarted = COIN_ACTIVE_LOW ? (lastCoinLevel == HIGH && now == LOW)
                                           : (lastCoinLevel == LOW && now == HIGH);
  if (pulseStarted) { coinPulses++; lastCoinPulseAt = millis(); }
  lastCoinLevel = now;
  if (coinPulses && millis() - lastCoinPulseAt >= COIN_GAP_MS) {
    if (coinPulses >= 1 && coinPulses <= 3) {
      const byte cents[4] = {0, 5, 10, 20};
      char event[12]; snprintf(event, sizeof(event), "COIN %u", cents[coinPulses]); sendLine(event);
    }
    coinPulses = 0;
  }
}

void checkKey() {
  bool now = digitalRead(KEY_PIN) == LOW;
  if (now != lastKeyState) { lastKeyState = now; sendLine(now ? "KEY ON" : "KEY OFF"); }
}

void setup() {
  Serial.begin(9600);
  pinMode(COIN_PIN, INPUT_PULLUP);
  pinMode(KEY_PIN, INPUT_PULLUP);
  pinMode(RELAY_PIN, OUTPUT); setRelay(false);
  for (byte i = 0; i < 5; i++) {
    pinMode(BUTTON_PINS[i], INPUT_PULLUP);
    pinMode(LED_PINS[i], OUTPUT); setLed(i, false);
    buttonRaw[i] = buttonStable[i] = digitalRead(BUTTON_PINS[i]);
    buttonChangedAt[i] = millis();
  }
  lastCoinLevel = digitalRead(COIN_PIN);
  lastKeyState = digitalRead(KEY_PIN) == LOW;
  lcd.init(); lcd.backlight(); showLcd("Automat startet", "Bitte warten");
  setLed(0, true);
  startupChangedAt = millis();
  sendLine("READY");
  sendLine(lastKeyState ? "KEY ON" : "KEY OFF");
}

void loop() {
  readSerial();
  updateStartupAnimation();
  checkButtons();
  checkCoin();
  checkKey();
}
