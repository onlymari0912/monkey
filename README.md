# MonkeyBusiness

Experimental e-amuse server for Polaris Chord.

## Usage

Run [start.bat (Windows)](start.bat) or [start.sh (Linux, MacOS)](start.sh)

[web interface](https://github.com/drmext/BounceTrippy/releases), [score import](utils/db)

**Note**: Playable means settings/scores *should* save and load. Events are not implemented.

## Troubleshooting

- Delete [or fix](start.bat#L9) `/.venv` if the server folder is moved or python is upgraded

- **URL Slash 1 (On)** [may still be required in rare cases](modules/__init__.py)

- **URL Slash 0 (Off)** may be required in other cases
