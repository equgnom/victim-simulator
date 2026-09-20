Dedicated sounds played when a keyword is heard (a rescuer calling out).

Drop .wav files in this folder. Any filename works; one is picked at random
each time, never the same one twice in a row. No restart needed for new files
to be used.

While this folder has no .wav files, the "cry" clips stand in for it
(trigger.reply_fallback_category in config.yaml), so a keyword is never
answered with silence. As soon as a .wav file appears here, these take over.

These are separate from shout/, cry/ and moan/, which are used for the
victim's own spontaneous calls (distress / weak mode).
