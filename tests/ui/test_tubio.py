"""
UI tests for Tubio using Playwright.
"""
from pathlib import Path

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


@pytest.fixture
def tubio_player_page(browser):
    """Load the real Tubio player script around a deterministic fake media element."""
    page = browser.new_page()
    page.set_content(
        """
        <div id="playlists">
          <section class="playlist-panel active"
                   data-playlist-name="Mix" data-playlist-kind="regular">
            <button class="btn-play-all"><i></i></button>
            <div class="playlist-accordion"
                 data-playlist-name="Mix" data-playlist-kind="regular">
              <article class="accordion-item playlist-track"
                       data-track-key="mix-0" data-audio-crc="101"
                       data-playlist="Mix" data-playlist-kind="regular"
                       data-title="First" data-thumbnail-url=""
                       data-is-cached="true"
                       data-trim-start="5" data-trim-end="2"
                       data-audio-src="/audio/101">
                <button class="track-play-btn"><i></i></button>
              </article>
              <article class="accordion-item playlist-track"
                       data-track-key="mix-1" data-audio-crc="202"
                       data-playlist="Mix" data-playlist-kind="regular"
                       data-title="Second" data-thumbnail-url=""
                       data-is-cached="false"
                       data-trim-start="0" data-trim-end="0"
                       data-audio-src="/audio/202">
                <button class="track-play-btn"><i></i></button>
              </article>
            </div>
          </section>
        </div>
        <div id="tubio-trackbar" data-active="false">
          <audio id="tubio-audio" preload="none"></audio>
          <span id="trackbar-title"></span>
          <span id="trackbar-playlist"></span>
          <button id="trackbar-playpause"><i></i></button>
          <input id="trackbar-scrubber" type="range">
          <span id="trackbar-time-current"></span>
          <span id="trackbar-time-duration"></span>
        </div>
        """
    )
    page.evaluate(
        """
        () => {
            const audio = document.getElementById('tubio-audio');
            audio._paused = true;
            audio._duration = NaN;
            audio._readyState = 0;
            audio._pauseCalls = 0;
            audio._playCalls = [];
            Object.defineProperty(audio, 'paused', {
                configurable: true,
                get: () => audio._paused
            });
            Object.defineProperty(audio, 'duration', {
                configurable: true,
                get: () => audio._duration
            });
            Object.defineProperty(audio, 'readyState', {
                configurable: true,
                get: () => audio._readyState
            });
            audio.load = () => {};
            audio.pause = () => {
                audio._pauseCalls += 1;
                audio._paused = true;
                audio.dispatchEvent(new Event('pause'));
            };
            audio.play = () => {
                audio._playCalls.push(audio.dataset.trackKey || audio.dataset.crc);
                return Promise.resolve();
            };
            window.scrollTo = () => {};
            Element.prototype.scrollIntoView = () => {};
        }
        """
    )
    static_dir = Path(__file__).parents[2] / "web_app" / "tubio" / "static"
    page.add_script_tag(path=str(static_dir / "api.js"))
    page.add_script_tag(path=str(static_dir / "player.js"))
    page.evaluate("window.Tubio.player.init()")
    yield page
    page.close()


def test_tubio_page_loads(tubio_page):
    """Test that Tubio page loads correctly."""
    expect(tubio_page).to_have_title("Tubio")


def test_player_exposes_a_small_namespaced_interface(tubio_player_page):
    assert tubio_player_page.evaluate(
        """
        () => Object.keys(window.Tubio?.player || {}).sort()
        """
    ) == ["handleAction", "init", "reconcileDom", "state"]


def test_sidebar_preferences_survive_library_reconciliation(browser):
    page = browser.new_page()
    page.set_content(
        """
        <div id="tubio-tab-content"
             data-sidebar-collapsed-storage-key="tubioSidebarCollapsed"
             data-sidebar-selected-storage-key="tubioSelectedPlaylist">
          <div id="playlists" class="tab-pane">
            <div class="tubio-layout">
              <button class="sidebar-item" data-tubio-action="select-playlist" data-playlist-slug="First"></button>
              <button class="sidebar-item" data-tubio-action="select-playlist" data-playlist-slug="Second"></button>
              <section class="playlist-panel" id="panel-First"></section>
              <section class="playlist-panel" id="panel-Second"></section>
              <div id="panel-empty"></div>
            </div>
          </div>
          <div id="search" class="tab-pane"></div>
          <div id="discover" class="tab-pane"></div>
        </div>
        <button id="toggle-sidebar" data-tubio-action="toggle-sidebar"></button>
        <div id="tubio-trackbar"></div>
        """
    )
    page.evaluate(
        """
        () => {
            const values = new Map();
            Object.defineProperty(window, 'sessionStorage', {
                configurable: true,
                value: {
                    getItem: key => values.has(key) ? values.get(key) : null,
                    setItem: (key, value) => values.set(key, String(value))
                }
            });
        }
        """
    )
    static_dir = Path(__file__).parents[2] / "web_app" / "tubio" / "static"
    page.add_script_tag(path=str(static_dir / "api.js"))
    page.add_script_tag(path=str(static_dir / "player.js"))
    page.add_script_tag(path=str(static_dir / "script.js"))
    page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")

    page.locator('[data-playlist-slug="Second"]').click()
    page.locator('#toggle-sidebar').click()
    page.evaluate(
        """
        window.Tubio.ui.replaceLibrary(`
          <div class="tubio-layout">
            <button class="sidebar-item" data-tubio-action="select-playlist" data-playlist-slug="First"></button>
            <button class="sidebar-item" data-tubio-action="select-playlist" data-playlist-slug="Second"></button>
            <section class="playlist-panel" id="panel-First"></section>
            <section class="playlist-panel" id="panel-Second"></section>
            <div id="panel-empty"></div>
          </div>
        `)
        """
    )

    assert page.evaluate(
        """
        () => ({
            selected: sessionStorage.getItem('tubioSelectedPlaylist'),
            collapsed: sessionStorage.getItem('tubioSidebarCollapsed'),
            selectedPanel: document.getElementById('panel-Second').classList.contains('active'),
            collapsedLayout: document.querySelector('.tubio-layout').classList.contains('sidebar-collapsed')
        })
        """
    ) == {
        "selected": "Second",
        "collapsed": "1",
        "selectedPanel": True,
        "collapsedLayout": True,
    }
    page.close()


def test_playlists_nav_present(tubio_page):
    """Test that Playlists nav item is present."""
    expect(tubio_page.locator("a", has_text="Playlists")).to_be_visible()


def test_search_nav_present(tubio_page):
    """Test that Search nav item is present."""
    expect(tubio_page.locator("a", has_text="Search")).to_be_visible()


def test_actions_dropdown_works(tubio_page):
    """Test that Actions dropdown opens and has items."""
    tubio_page.click("button:has-text('Actions')")
    
    # Check that dropdown menu is visible and has items
    expect(tubio_page.locator(".dropdown-menu:visible")).to_be_visible()
    # At least one dropdown item should be present
    assert tubio_page.locator(".dropdown-menu .dropdown-item").count() > 0


def test_search_tab_content(tubio_page):
    """Test that search tab has search input."""
    # Click on Search tab
    tubio_page.click("a:has-text('Search')")

    # Check for search input
    expect(tubio_page.locator("input[placeholder*='Search']")).to_be_visible()


@pytest.mark.parametrize("viewport_width", [375, 743])
def test_surprise_playlist_rows_stay_within_narrow_viewport(
    tubio_page, viewport_width
):
    """Long uncached track rows must not push controls or chevrons off-screen."""
    tubio_page.set_viewport_size({"width": viewport_width, "height": 600})
    tubio_page.locator("#tubio-tab-content").evaluate(
        """
        container => {
            container.innerHTML = `
                <div class="tubio-discover-content">
                  <div class="surprise-playlist">
                    <section class="playlist-panel active">
                      <header class="playlist-panel-header">
                        <div class="playlist-panel-title">
                          <i class="bi bi-stars me-2"></i>
                          <span>Surprise Playlist</span>
                          <span class="badge bg-light ms-2 playlist-count">6 songs</span>
                        </div>
                        <div class="playlist-panel-controls">
                          <button class="btn btn-sm btn-play-all">Play</button>
                          <button class="btn btn-sm btn-primary">Save playlist</button>
                        </div>
                      </header>
                      <div class="playlist-panel-body">
                        ${[
                            'Owl City - Fireflies (Lyrics)',
                            'Sparkle - Your Name [Kimi no Na wa] Full Version',
                            'OneRepublic - Counting Stars (Official Music Video)'
                        ].map((title, index) => `
                          <article class="accordion-item playlist-track">
                            <h2 class="accordion-header">
                              <div class="playlist-track-header">
                                <div class="playlist-track-select playlist-track-select-slot"></div>
                                <button class="btn btn-sm track-play-btn">▶</button>
                                <button class="accordion-button collapsed playlist-track-expand">
                                  <span class="playlist-track-name">${title}</span>
                                </button>
                              </div>
                            </h2>
                          </article>
                        `).join('')}
                      </div>
                    </section>
                  </div>
                </div>`;
        }
        """
    )

    layout = tubio_page.evaluate(
        """
        () => {
            const panel = document.querySelector('.playlist-panel');
            const panelRight = panel.getBoundingClientRect().right;
            const controlsRight = document.querySelector(
                '.playlist-panel-controls'
            ).getBoundingClientRect().right;
            const rowRights = [...document.querySelectorAll(
                '.playlist-track-header'
            )].map(row => row.getBoundingClientRect().right);
            const buttonRights = [...document.querySelectorAll(
                '.playlist-track-expand'
            )].map(button => button.getBoundingClientRect().right);
            return {
                viewportRight: window.innerWidth,
                panelRight,
                controlsRight,
                rowRights,
                buttonRights
            };
        }
        """
    )

    assert layout["panelRight"] <= layout["viewportRight"] + 1
    assert layout["controlsRight"] <= layout["panelRight"] + 1
    assert all(right <= layout["panelRight"] + 1 for right in layout["rowRights"])
    assert max(layout["buttonRights"]) - min(layout["buttonRights"]) <= 1


def test_suggest_more_switches_to_discover_and_posts_track_seed(
    tubio_player_page,
):
    result = tubio_player_page.evaluate(
        """
        async () => {
            document.getElementById('playlists').classList.add('tab-pane');
            document.body.insertAdjacentHTML('afterbegin', `
                <a id="playlists-nav-tab"></a>
                <a id="search-nav-tab"></a>
                <a id="discover-nav-tab"></a>
                <div id="discover" class="tab-pane">
                    <div id="surprise-status"></div>
                    <div id="surprise-playlist"
                         data-buffer-size="5"
                         data-cache-poll-interval-ms="750"></div>
                </div>
            `);
            const track = document.querySelector('[data-track-key="mix-0"]');
            track.insertAdjacentHTML(
                'beforeend',
                '<button id="suggest-more">Suggest more</button>'
            );

            const posts = [];
            window.Tubio.ui = {
                switchTab: tab => { window.location.hash = `#${tab}`; },
                decorate: () => {},
                notify: () => {}
            };
            window.Tubio.api.post = async (url, fields = {}) => {
                posts.push({ url, fields });
                return {
                    playlist: {
                        audio_crcs: [303],
                        html: '<section id="seeded-playlist"></section>'
                    }
                };
            };

            await window.Tubio.player.handleAction(
                'suggest-more',
                document.getElementById('suggest-more')
            );

            return {
                hash: window.location.hash,
                posts,
                rendered: Boolean(document.getElementById('seeded-playlist')),
                status: document.getElementById('surprise-status').textContent
            };
        }
        """
    )

    assert result == {
        "hash": "#discover",
        "posts": [{
            "url": "/tubio/surprise",
            "fields": {"seed_crc": "101"},
        }],
        "rendered": True,
        "status": "1 track ready to play.",
    }


def test_audio_elements_have_preload_none(tubio_page):
    """Audio elements must have preload=none to avoid overwhelming server on page load."""
    audio_elements = tubio_page.locator("audio")
    count = audio_elements.count()

    for i in range(count):
        preload = audio_elements.nth(i).get_attribute("preload")
        assert preload == "none", f"Audio element {i} has preload='{preload}', expected 'none'"


def test_no_failed_requests_on_page_load(tubio_page):
    """Page load should not trigger failed audio requests (503 errors)."""
    failed_requests = []

    def handle_response(response):
        if "/tubio/audio/" in response.url and response.status >= 400:
            failed_requests.append((response.url, response.status))

    tubio_page.on("response", handle_response)
    tubio_page.reload()
    tubio_page.wait_for_load_state("networkidle")

    assert len(failed_requests) == 0, f"Failed audio requests on page load: {failed_requests}"


def test_trackbar_volume_controls_present(tubio_page):
    """Persistent player exposes volume controls in a compact popover."""
    expect(tubio_page.locator("#trackbar-mute")).to_be_visible()
    expect(tubio_page.locator("#trackbar-volume")).not_to_be_visible()
    tubio_page.locator("#trackbar-mute").focus()
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()


def test_trackbar_actions_align_with_main_controls(tubio_page):
    """Volume and playlist controls align with the main playback controls."""
    positions = tubio_page.evaluate("""
        () => {
            const play = document.getElementById('trackbar-playpause').getBoundingClientRect();
            const volume = document.getElementById('trackbar-mute').getBoundingClientRect();
            const playlist = document.querySelector('.trackbar-actions > button').getBoundingClientRect();
            const trackbar = document.getElementById('tubio-trackbar').getBoundingClientRect();
            return {
                playCenterY: play.top + play.height / 2,
                volumeCenterY: volume.top + volume.height / 2,
                playlistCenterY: playlist.top + playlist.height / 2,
                playCenterX: play.left + play.width / 2,
                trackbarCenterX: trackbar.left + trackbar.width / 2,
                playlistRight: playlist.right,
                trackbarRight: trackbar.right
            };
        }
    """)

    assert abs(positions["playCenterY"] - positions["volumeCenterY"]) <= 2
    assert abs(positions["playCenterY"] - positions["playlistCenterY"]) <= 2
    assert abs(positions["playCenterX"] - positions["trackbarCenterX"]) <= 24
    assert positions["trackbarRight"] - positions["playlistRight"] <= 24


def test_trackbar_volume_hover_path_stays_open(tubio_page):
    """Desktop hover path from the volume icon to slider keeps the popover open."""
    tubio_page.locator("#trackbar-mute").hover()
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()

    positions = tubio_page.evaluate("""
        () => {
            const button = document.getElementById('trackbar-mute').getBoundingClientRect();
            const popover = document.getElementById('trackbar-volume-popover').getBoundingClientRect();
            return {
                x: button.left + button.width / 2,
                bridgeY: popover.bottom + ((button.top - popover.bottom) / 2),
                sliderY: popover.top + popover.height / 2
            };
        }
    """)
    tubio_page.mouse.move(positions["x"], positions["bridgeY"])
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()
    tubio_page.mouse.move(positions["x"], positions["sliderY"])
    expect(tubio_page.locator("#trackbar-volume")).to_be_visible()


def test_mobile_trackbar_actions_align_with_track_info(page, test_server):
    """Mobile volume and playlist buttons sit on the track info row."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto(f"{test_server}/tubio")
    page.wait_for_load_state("networkidle")

    positions = page.evaluate("""
        () => {
            const info = document.querySelector('.trackbar-info').getBoundingClientRect();
            const volume = document.getElementById('trackbar-mute').getBoundingClientRect();
            const playlist = document.querySelector('.trackbar-actions > button').getBoundingClientRect();
            return {
                infoCenterY: info.top + info.height / 2,
                volumeCenterY: volume.top + volume.height / 2,
                playlistCenterY: playlist.top + playlist.height / 2,
                infoLeft: info.left,
                playlistRight: playlist.right,
                trackbarRight: document.getElementById('tubio-trackbar').getBoundingClientRect().right
            };
        }
    """)

    assert abs(positions["infoCenterY"] - positions["volumeCenterY"]) <= 4
    assert abs(positions["infoCenterY"] - positions["playlistCenterY"]) <= 4
    assert positions["infoLeft"] < positions["playlistRight"]
    assert positions["trackbarRight"] - positions["playlistRight"] <= 16


def test_trackbar_title_scrolls_when_overflowing(tubio_page):
    """Long track titles get scrolling treatment in the compact trackbar."""
    tubio_page.evaluate("""
        () => {
            const wrap = document.querySelector('.trackbar-title-wrap');
            const title = document.getElementById('trackbar-title');
            wrap.style.width = '96px';
            title.textContent = 'A very long track title that cannot fit in the available mobile space';
            window.dispatchEvent(new Event('resize'));
        }
    """)

    assert tubio_page.locator("#trackbar-title").evaluate(
        "(el) => el.classList.contains('is-overflowing')"
    )


def test_playlist_expanded_card_actions_are_cleaned_up(app):
    """Expanded playlist cards omit the label and use the bordered action style."""
    import web_app.__main__  # noqa: F401

    track = {
        "crc": 123,
        "title": "Expanded track",
        "thumbnail_url": "",
        "source_url": "https://example.test/source",
        "video_id": "video000001",
        "trim_start_s": 0,
        "trim_end_s": 0,
        "is_cached": True,
        "is_favourite": False,
    }
    with app.test_request_context("/tubio/"):
        template = app.jinja_env.get_template("playlist_components.html")
        html = str(template.module.playlist_panel("Favourites", [track]))

    assert "Full Title:" not in html
    assert html.count("track-action-btn") == 6


def test_trackbar_volume_applies_to_audio_elements(tubio_page):
    """Changing the volume slider updates audio elements and persists the value."""
    tubio_page.evaluate("""
        const audio = document.createElement('audio');
        audio.id = 'audio-volume-test';
        document.body.appendChild(audio);
        window.Tubio.player.reconcileDom();
    """)

    tubio_page.locator("#trackbar-volume").evaluate("(el) => { el.value = '35'; el.dispatchEvent(new Event('input', { bubbles: true })); }")

    assert tubio_page.evaluate("document.getElementById('audio-volume-test').volume") == pytest.approx(0.35)
    assert tubio_page.evaluate("localStorage.getItem(document.getElementById('tubio-trackbar').dataset.volumeStorageKey)") == "35"


def test_player_commits_track_only_after_playing_event(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const item = document.querySelector('[data-track-key="mix-0"]');
            window.Tubio.player.handleAction('toggle-track', item);
            await Promise.resolve();
            const beforePlaying = {
                crc: window.Tubio.player.state().currentCrc,
                highlighted: item.classList.contains('track-playing'),
                buttonPlaying: item.querySelector('.track-play-btn').classList.contains('btn-success'),
                pauseCalls: document.getElementById('tubio-audio')._pauseCalls
            };
            document.getElementById('tubio-audio')._paused = false;
            document.getElementById('tubio-audio').dispatchEvent(new Event('playing'));
            return {
                beforePlaying,
                afterPlaying: {
                    crc: window.Tubio.player.state().currentCrc,
                    highlighted: item.classList.contains('track-playing'),
                    buttonPlaying: item.querySelector('.track-play-btn').classList.contains('btn-success')
                }
            };
        }
        """
    )

    assert result["beforePlaying"] == {
        "crc": None,
        "highlighted": False,
        "buttonPlaying": False,
        "pauseCalls": 0,
    }
    assert result["afterPlaying"] == {
        "crc": "101",
        "highlighted": True,
        "buttonPlaying": True,
    }


def test_rejected_play_does_not_claim_success_or_skip_track(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const audio = document.getElementById('tubio-audio');
            audio.play = () => Promise.reject(new DOMException('blocked', 'NotAllowedError'));
            const item = document.querySelector('[data-track-key="mix-0"]');
            window.Tubio.player.handleAction('toggle-track', item);
            await Promise.resolve();
            await Promise.resolve();
            return {
                crc: window.Tubio.player.state().currentCrc,
                loadedKey: audio.dataset.trackKey,
                highlighted: item.classList.contains('track-playing'),
                buttonPlaying: item.querySelector('.track-play-btn').classList.contains('btn-success')
            };
        }
        """
    )

    assert result == {
        "crc": None,
        "loadedKey": "mix-0",
        "highlighted": False,
        "buttonPlaying": False,
    }


def test_playlist_handoff_is_immediate_and_uses_exact_queue_entry(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const audio = document.getElementById('tubio-audio');
            let transitionTimers = 0;
            window.setTimeout = () => { transitionTimers += 1; };
            audio._duration = 30;
            audio.play = () => {
                audio._playCalls.push(audio.dataset.trackKey);
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };

            window.Tubio.player.handleAction(
                'toggle-playlist',
                document.querySelector('.playlist-panel')
            );
            audio._paused = true;
            audio.dispatchEvent(new Event('ended'));
            await Promise.resolve();

            return {
                calls: audio._playCalls,
                currentCrc: window.Tubio.player.state().currentCrc,
                currentIndex: window.Tubio.player.state().playlistIndex,
                transitionTimers
            };
        }
        """
    )

    assert result == {
        "calls": ["mix-0", "mix-1"],
        "currentCrc": "202",
        "currentIndex": 1,
        "transitionTimers": 0,
    }


def test_regular_playlist_converts_and_prefetches_next_track(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const requests = [];
            window.fetch = async (url, options = {}) => {
                requests.push({
                    url: String(url),
                    method: options.method || 'GET'
                });
                if (String(url).endsWith('/cache')) {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ success: true, is_cached: true })
                    };
                }
                return {
                    ok: true,
                    status: 200,
                    blob: async () => new Blob(['audio'])
                };
            };

            const audio = document.getElementById('tubio-audio');
            audio.play = () => {
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };

            window.Tubio.player.handleAction(
                'toggle-playlist',
                document.querySelector('.playlist-panel')
            );
            await new Promise(resolve => setTimeout(resolve, 0));

            return {
                requests,
                nextIsCached: document.querySelector(
                    '[data-track-key="mix-1"]'
                ).dataset.isCached
            };
        }
        """
    )

    assert result == {
        "requests": [
            {"url": "/tubio/audio/202/cache", "method": "POST"},
            {"url": "/audio/202", "method": "GET"},
        ],
        "nextIsCached": "true",
    }


def test_surprise_playlist_converts_and_prefetches_next_track(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const items = document.querySelectorAll('.playlist-track');
            items.forEach(item => {
                item.dataset.playlist = 'Surprise Playlist';
                item.dataset.playlistKind = 'surprise';
            });

            const requests = [];
            window.fetch = async (url, options = {}) => {
                requests.push({
                    url: String(url),
                    method: options.method || 'GET'
                });
                if (String(url).endsWith('/cache')) {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ success: true, is_cached: true })
                    };
                }
                return {
                    ok: true,
                    status: 200,
                    blob: async () => new Blob(['audio'])
                };
            };

            const audio = document.getElementById('tubio-audio');
            audio.play = () => {
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };

            window.Tubio.player.handleAction('toggle-surprise-track', items[0]);
            await new Promise(resolve => setTimeout(resolve, 0));

            return {
                requests,
                nextIsCached: document.querySelector(
                    '[data-track-key="mix-1"]'
                ).dataset.isCached
            };
        }
        """
    )

    assert result == {
        "requests": [
            {"url": "/tubio/audio/202/cache", "method": "POST"},
            {"url": "/audio/202", "method": "GET"},
        ],
        "nextIsCached": "true",
    }


def test_duplicate_track_occurrences_each_play_in_queue_order(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const items = document.querySelectorAll('.playlist-track');
            items[1].dataset.audioCrc = '101';
            items[1].dataset.audioSrc = '/audio/101';

            const audio = document.getElementById('tubio-audio');
            audio._duration = 30;
            audio.play = () => {
                audio._playCalls.push(audio.dataset.trackKey);
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };

            window.Tubio.player.handleAction(
                'toggle-playlist',
                document.querySelector('.playlist-panel')
            );
            audio._paused = true;
            audio.dispatchEvent(new Event('ended'));
            await Promise.resolve();

            return {
                calls: audio._playCalls,
                currentCrc: window.Tubio.player.state().currentCrc,
                currentIndex: window.Tubio.player.state().playlistIndex
            };
        }
        """
    )

    assert result == {
        "calls": ["mix-0", "mix-1"],
        "currentCrc": "101",
        "currentIndex": 1,
    }


def test_media_session_play_retries_failed_loaded_track(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const handlers = {};
            const mediaSession = {
                metadata: null,
                playbackState: 'none',
                setActionHandler: (action, handler) => { handlers[action] = handler; },
                setPositionState: () => {}
            };
            Object.defineProperty(navigator, 'mediaSession', {
                configurable: true,
                value: mediaSession
            });
            window.Tubio.player.reconcileDom();

            const audio = document.getElementById('tubio-audio');
            audio.play = () => Promise.reject(
                new DOMException('background handoff failed', 'NotAllowedError')
            );
            window.Tubio.player.handleAction(
                'toggle-track',
                document.querySelector('[data-track-key="mix-0"]')
            );
            await Promise.resolve();
            await Promise.resolve();
            const stateAfterFailure = mediaSession.playbackState;

            audio.play = () => {
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };
            handlers.play();
            await Promise.resolve();

            return {
                stateAfterFailure,
                stateAfterRetry: mediaSession.playbackState,
                currentCrc: window.Tubio.player.state().currentCrc
            };
        }
        """
    )

    assert result == {
        "stateAfterFailure": "paused",
        "stateAfterRetry": "playing",
        "currentCrc": "101",
    }


def test_play_pause_seek_and_trim_share_confirmed_player_state(tubio_player_page):
    result = tubio_player_page.evaluate(
        """
        async () => {
            const audio = document.getElementById('tubio-audio');
            audio._duration = 30;
            audio.play = () => {
                audio._playCalls.push(audio.dataset.trackKey);
                audio._paused = false;
                audio.dispatchEvent(new Event('playing'));
                return Promise.resolve();
            };

            window.Tubio.player.handleAction(
                'toggle-track',
                document.querySelector('[data-track-key="mix-0"]')
            );
            audio._metadataReady = true;
            audio._readyState = 1;
            audio.dispatchEvent(new Event('loadedmetadata'));
            const trimmedStart = audio.currentTime;

            const scrubber = document.getElementById('trackbar-scrubber');
            scrubber.value = '100';
            scrubber.dispatchEvent(new Event('input'));
            const trimmedEnd = audio.currentTime;

            window.Tubio.player.handleAction('toggle-playback');
            const paused = audio._paused;
            window.Tubio.player.handleAction('toggle-playback');
            await Promise.resolve();

            return {
                trimmedStart,
                trimmedEnd,
                paused,
                resumedAt: audio.currentTime,
                playCalls: audio._playCalls.length,
                currentCrc: window.Tubio.player.state().currentCrc
            };
        }
        """
    )

    assert result == {
        "trimmedStart": 5,
        "trimmedEnd": 28,
        "paused": True,
        "resumedAt": 5,
        "playCalls": 2,
        "currentCrc": "101",
    }
