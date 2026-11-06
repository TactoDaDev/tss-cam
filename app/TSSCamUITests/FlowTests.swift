// Drives the real app in the simulator against the real receiver + a fake BeamNG running on the CI Mac
// (tools/ci_e2e.py). The harness checks on its side that every tap arrived as the right command; this test checks
// the app's side: it connects, shows the game's capabilities, and its controls respond.
import XCTest

final class FlowTests: XCTestCase {
    override func setUp() { continueAfterFailure = false }

    func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    func testTapThroughTheApp() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-host", "127.0.0.1", "-fakeTracking"]
        app.launch()

        // capabilities arrived: the camera mode pills come from the game
        let chase = app.buttons["mode-chase"]
        XCTAssertTrue(chase.waitForExistence(timeout: 30), "no capabilities from the receiver")
        shot(app, "1-connected")

        // film, pick a camera mode, recenter
        let shutter = app.descendants(matching: .any)["shutter"]
        XCTAssertTrue(shutter.exists)
        shutter.tap()
        sleep(1)
        chase.tap()
        sleep(1)
        app.buttons["recenter"].tap()
        sleep(1)

        // hold the lift stick up for a second
        let stick = app.descendants(matching: .any)["stick-lift"]
        XCTAssertTrue(stick.waitForExistence(timeout: 5))
        let centre = stick.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
        let up = stick.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.05))
        centre.press(forDuration: 0.1, thenDragTo: up, withVelocity: .slow, thenHoldForDuration: 1.0)
        shot(app, "2-filming")

        // the car sheet: a door and the horn (held)
        app.buttons["tab-CAR"].tap()
        let door = app.descendants(matching: .any)["ctl-panel:door_FL"]
        XCTAssertTrue(door.waitForExistence(timeout: 10), "car controls missing")
        shot(app, "3-car")
        door.tap()
        sleep(1)
        let horn = app.descendants(matching: .any)["ctl-horn"]
        XCTAssertTrue(horn.exists)
        horn.press(forDuration: 1.0)
        sleep(2) // let the last packets go out before the harness counts
        shot(app, "4-done")
    }
}
