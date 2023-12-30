import SwiftUI
import Foundation


struct ContentView: View {
    @StateObject private var goals_fetcher = GoalsFetcher()
    @State var curr_goal: Goal?
    
    var body: some View {
        // I have absolutely no idea why this HStack is necessary
        // without it the content view doesn't get updated immediately
        // on curr_goal change
        HStack {
            if curr_goal == nil {
                daily_view()
            } else {
                description_view()
            }
        }
    }
    
    func daily_view() -> some View {
        VStack(alignment: .leading) {
            HStack {
                Spacer()
                Text("Todoist2")
                    .font(.largeTitle)
                    .fontWeight(.bold)
                    .foregroundColor(.blue)
                    .padding(.vertical, 10)
                Spacer()
                Button(action: {
                    goals_fetcher.fetch_goals()
                }) {
                    Text("Refresh Goals")
                        .foregroundColor(.white)
                        .padding()
                        .background(Color.blue)
                        .cornerRadius(8)
                }
                Spacer()
            }.padding()
            Spacer()
            HStack() {
                VStack(alignment: .leading) {
                    ForEach(goals_fetcher.goals.indices, id: \.self) { index in
                        HStack() {
                            let goal = goals_fetcher.goals[index]
                            Button(action: {
                                curr_goal = goal
                            })
                            {
                                Text(goal.name)                        .foregroundColor(.white)
                                    .padding()
                                    .background(Color.blue)
                                    .cornerRadius(8)
                                    .multilineTextAlignment(.leading)
                            }.padding(0.4)
                             .padding([.leading], 10)
                        }
                    }
                }.frame(
                    minWidth: 0,
                    maxWidth: .infinity,
                    minHeight: 0,
                    maxHeight: .infinity,
                    alignment: .topLeading)
            }
            Spacer()
        }
    }
    
    func description_view() -> some View {
        VStack(alignment: .leading) {
            HStack {
                Spacer()
                Text("Todoist2")
                    .font(.largeTitle)
                    .fontWeight(.bold)
                    .foregroundColor(.blue)
                    .padding(.vertical, 10)
                Spacer()
                Button(action: {
                    curr_goal = nil
                }) {
                    Text("Return")
                        .foregroundColor(.white)
                        .padding()
                        .background(Color.blue)
                        .cornerRadius(8)
                }
                Spacer()
            }.padding()
            Spacer()
            HStack() {
                VStack(alignment: .leading) {
                    Text(curr_goal?.name ?? "")
                        .font(.title)
                        .fontWeight(.bold)
                        .foregroundColor(.blue)
                        .padding()

                    Text(curr_goal?.description ?? "")
                        .foregroundColor(.gray).padding()
                    Image("Image").resizable().scaledToFill()
                        .edgesIgnoringSafeArea(.all)
                }
                .frame(
                    minWidth: 0,
                    maxWidth: .infinity,
                    minHeight: 0,
                    maxHeight: .infinity,
                    alignment: .topLeading)
            }
            Spacer()
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
