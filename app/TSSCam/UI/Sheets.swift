// Car controls (built from the capabilities the game sends: each car's own doors, its lights...) and Connect.
import SwiftUI

let GROUP_ORDER = ["doors": "DOORS", "lights": "LIGHTS", "horn": "HORN", "car": "CAR"]

struct CarSheet: View {
    @EnvironmentObject var link: Link
    @State private var slowmo: Double = 1

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if let caps = link.caps {
                        header(caps)
                        ForEach(["doors", "lights", "horn", "car"], id: \.self) { g in
                            let items = caps.controls.filter { $0.group == g }
                            if !items.isEmpty { group(GROUP_ORDER[g] ?? g.uppercased(), items) }
                        }
                        cars
                        time(caps)
                    } else {
                        Text(link.live ? "This receiver has no car controls (stock AcHandCam?)." : "Connect to the TSS Cam receiver first.")
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(18)
            }
            .navigationTitle("Car")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
    }

    private func header(_ caps: Caps) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text((caps.car.isEmpty ? "No car" : caps.car).uppercased()).font(.readout(26))
            Text(caps.connected ? "\(GAME_LABELS[caps.game ?? ""] ?? "Game") · \(caps.controls.count) controls" : "Game not connected")
                .font(.system(size: 13)).foregroundStyle(.secondary)
            HStack {
                pill(caps.engaged ? "Stop filming" : "Film", hero: !caps.engaged) { link.command("cam.toggle") }
                pill(caps.clean ? "Show HUD" : "Hide HUD") { link.command("clean", ["on": !caps.clean]) }
            }.padding(.top, 6)
        }
    }

    private func group(_ title: String, _ items: [Control]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.race(11)).tracking(1.6).foregroundStyle(Livery.sky)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 10)], spacing: 10) {
                ForEach(items) { c in ControlButton(control: c) }
            }
        }
    }

    private var cars: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("FILM WHICH CAR").font(.race(11)).tracking(1.6).foregroundStyle(Livery.sky)
            HStack {
                pill("Previous") { link.command("car.prev") }
                pill("Mine") { link.command("car.player") }
                pill("Next") { link.command("car.next") }
            }
        }
    }

    private func time(_ caps: Caps) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("SLOW MOTION · \(String(format: "%.2f", slowmo))x").font(.race(11)).tracking(1.6).foregroundStyle(Livery.sky)
            Slider(value: $slowmo, in: 0.05...1, onEditingChanged: { editing in
                if !editing { link.command("time", ["speed": slowmo]) }
            })
            .tint(Livery.red)
        }
        .onAppear { slowmo = caps.time }
    }

    private func pill(_ title: String, hero: Bool = false, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.system(size: 14, weight: .bold))
                .padding(.horizontal, 16).padding(.vertical, 9)
                .foregroundStyle(hero ? Color.black : Color.white)
                .background(hero ? Color.white : Color.white.opacity(0.1), in: Capsule())
                .overlay(Capsule().stroke(Color.white.opacity(hero ? 0 : 0.15)))
        }
    }
}

/// One car control: toggles and presses send on tap; "hold" (horn) sends down and up; cycles show their state.
struct ControlButton: View {
    @EnvironmentObject var link: Link
    let control: Control
    @State private var holding = false

    private var title: String {
        if control.kind == "cycle", let v = control.value, v >= 0, v < control.states.count {
            return "\(control.label): \(control.states[v])"
        }
        return control.label
    }

    private var face: some View {
        let on = (control.value ?? 0) > 0 || holding
        return Text(title)
            .font(.system(size: 13, weight: .semibold)).multilineTextAlignment(.center)
            .frame(maxWidth: .infinity, minHeight: 46)
            .padding(.horizontal, 6)
            .foregroundStyle(Color.white)
            .background(on ? Livery.red.opacity(0.85) : Color.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(on ? Livery.red : Color.white.opacity(0.14)))
            .contentShape(Rectangle())
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(title)
            .accessibilityIdentifier("ctl-\(control.id)")
            .accessibilityAddTraits(.isButton)
    }

    @ViewBuilder var body: some View {
        if control.kind == "hold" {
            face.gesture(holdGesture)
        } else {
            face.onTapGesture {
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                link.command("car", ["id": control.id])
            }
        }
    }

    private var holdGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                if !holding {
                    holding = true
                    link.command("car", ["id": control.id, "down": true])
                }
            }
            .onEnded { _ in
                holding = false
                link.command("car", ["id": control.id, "down": false])
            }
    }
}

struct ConnectSheet: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var link: Link
    @EnvironmentObject var logs: AppLog
    @State private var address = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("192.168.x.x", text: $address)
                        .keyboardType(.numbersAndPunctuation)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button(link.live ? "Reconnect" : "Connect") {
                        model.connect(to: address)
                    }.disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
                } header: {
                    Text("PC address")
                } footer: {
                    Text("Open TSS Cam on the PC: its window shows the address. Phone and PC on the same Wi-Fi; allow Local Network when iOS asks.")
                }
                Section("Status") {
                    LabeledContent("Link", value: link.status)
                    if let r = link.rttMs { LabeledContent("Round trip", value: "\(Int(r)) ms") }
                    LabeledContent("Tracking", value: model.trackingText)
                    LabeledContent("Game", value: link.caps?.game.flatMap { GAME_LABELS[$0] } ?? "-")
                }
                Section("Log") {
                    ForEach(Array(logs.lines.suffix(80).enumerated()), id: \.offset) { _, line in
                        Text(line).font(.system(size: 11, design: .monospaced))
                    }
                }
            }
            .navigationTitle("Connect")
            .navigationBarTitleDisplayMode(.inline)
            .onAppear { address = model.host }
        }
    }
}
