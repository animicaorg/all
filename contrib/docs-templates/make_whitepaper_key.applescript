-- Animica Whitepaper Keynote template (safe plain-text AppleScript)
-- Usage (in shell before running this script):
--   export OUT_PATH="$(pwd)/contrib/docs-templates/whitepaper.key"
--   osascript contrib/docs-templates/make_whitepaper_key.applescript

on rgb16(r8, g8, b8)
	set r16 to r8 * 257
	set g16 to g8 * 257
	set b16 to b8 * 257
	return {r16, g16, b16}
end rgb16

set savePath to (system attribute "OUT_PATH")
if savePath is missing value or savePath = "" then
	error "Missing OUT_PATH env var. In shell: export OUT_PATH=\"$(pwd)/contrib/docs-templates/whitepaper.key\""
end if

-- Brand colors
set mint to my rgb16(94, 234, 212) -- #5EEAD4
set ink to my rgb16(11, 13, 18)    -- #0B0D12
set slate to my rgb16(15, 23, 42)  -- #0F172A
set slate500 to my rgb16(71, 85, 105)
set slate600 to my rgb16(100, 116, 139)
set white to {65535, 65535, 65535}

tell application "Keynote"
	activate
	-- Create new doc (default theme), then set size
	set theDoc to make new document
	set slide size of theDoc to {1920, 1080}

	-- ===== Cover slide =====
	tell slide 1 of theDoc
		set background fill type to color fill
		set background color to white

		-- Mint pill
		set brandPill to make new shape
		set shape type of brandPill to rounded rectangle
		set position of brandPill to {160, 140}
		set width of brandPill to 160
		set height of brandPill to 40
		set fill type of brandPill to color fill
		set fill color of brandPill to mint
		try
			set corner radius of brandPill to 10
		end try
		set object text of brandPill to "ANIMICA"
		tell object text of brandPill
			set color to ink
			set size to 20
			try
				set font to "Helvetica-Bold"
			end try
			set alignment to center
		end tell

		-- Title
		set titleItem to the default title item
		set object text of titleItem to "Whitepaper Title"
		tell object text of titleItem
			set color to slate
			set size to 72
			try
				set font to "Helvetica-Bold"
			end try
			set alignment to left
		end tell
		set position of titleItem to {160, 210}
		set width of titleItem to 1600

		-- Subtitle
		set bodyItem to the default body item
		set object text of bodyItem to "Subtitle goes here"
		tell object text of bodyItem
			set color to slate500
			set size to 28
			try
				set font to "Helvetica"
			end try
			set alignment to left
		end tell
		set position of bodyItem to {160, 320}
		set width of bodyItem to 1400

		-- Meta line
		set metaBox to make new text item
		set position of metaBox to {160, 390}
		set width of metaBox to 1400
		set height of metaBox to 40
		set object text of metaBox to ("Version v1.0.0 · " & (do shell script "date +%Y-%m-%d") & " · Authors: Your Name")
		tell object text of metaBox
			set color to slate600
			set size to 18
			try
				set font to "Helvetica"
			end try
		end tell

		-- Accent ring + spark
		set ring to make new shape
		set shape type of ring to oval
		set position of ring to {60, 960}
		set width of ring to 60
		set height of ring to 60
		set fill type of ring to no fill
		set stroke color of ring to mint
		set stroke width of ring to 6

		set spark to make new shape
		set shape type of spark to oval
		set position of spark to {92, 972}
		set width of spark to 12
		set height of spark to 12
		set fill type of spark to color fill
		set fill color of spark to mint
	end tell

	-- ===== Section slide =====
	set s2 to make new slide at end of slides of theDoc
	tell s2
		set background fill type to color fill
		set background color to white
		set t to make new text item
		set position of t to {240, 420}
		set width of t to 1440
		set height of t to 160
		set object text of t to "Section Title"
		tell object text of t
			set color to slate
			set size to 64
			try
				set font to "Helvetica-Bold"
			end try
			set alignment to center
		end tell
	end tell

	-- ===== Content slide =====
	set s3 to make new slide at end of slides of theDoc
	tell s3
		set background fill type to color fill
		set background color to white

		set hd to make new text item
		set position of hd to {160, 140}
		set width of hd to 1600
		set object text of hd to "Introduction"
		tell object text of hd
			set color to slate
			set size to 44
			try
				set font to "Helvetica-Bold"
			end try
		end tell

		set bullets to make new text item
		set position of bullets to {160, 220}
		set width of bullets to 1600
		set object text of bullets to "• Background and motivation
• Design goals & constraints
• Threat model overview"
		tell object text of bullets
			set color to slate
			set size to 28
			try
				set font to "Helvetica"
			end try
		end tell
	end tell

	-- ===== Quote slide =====
	set s4 to make new slide at end of slides of theDoc
	tell s4
		set background fill type to color fill
		set background color to white
		set q to make new text item
		set position of q to {280, 360}
		set width of q to 1360
		set object text of q to "“One idea per slide. Keep it crisp.”"
		tell object text of q
			set color to slate
			set size to 44
			try
				set font to "Helvetica-Oblique"
			end try
			set alignment to center
		end tell
	end tell

	set outAlias to POSIX file savePath as alias
save theDoc in outAlias
	close theDoc saving no
end tell

