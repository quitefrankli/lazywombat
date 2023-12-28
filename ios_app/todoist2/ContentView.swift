import SwiftUI
import AWSS3
import Yams
import Foundation


struct ResponseJSON : Codable
{
    let goals: Array<Goal>
    let edited: Int
}

struct Goal : Codable
{
    let name: String
    let daily: Int
    init(name: String, daily: Int)
    {
        self.name = name
        self.daily = daily
    }
    
    private enum CodingKeys: String, CodingKey {
        case name = "name"
        case daily = "daily"
    }
}

class GoalsFetcher
{
    private var goals: Array<Goal> = []
    private var semaphore: DispatchSemaphore = DispatchSemaphore(value: 0)
    private var data: Data?
    
    func async_loader() async throws
    {
        let client = try S3Client(region: "ap-southeast-2")
        let input = GetObjectInput(
            bucket: "todoist2",
            key: ".todoist2_goals.yaml"
        )
        let output = try await client.getObject(input: input)
        guard let body = output.body,
              let data2 = try await body.readData() else
        {
            throw fatalError("couldn't read data in body")
        }
        self.data = data2
        semaphore.signal()
    }
    
    func sync_loader() throws
    {
        semaphore = DispatchSemaphore(value: 0)
        Task {
            do {
                try await async_loader()
            } catch {
                print("Error info: \(error)")
            }
        }
        semaphore.wait()
        
        let str = String(decoding: data!, as: UTF8.self)
        let decoder = YAMLDecoder()
        let loadedDictionary = try Yams.load(yaml: str)
        let decoded: ResponseJSON = try decoder.decode(ResponseJSON.self, from: str)
        
        
        self.goals = []
        let today = Date()
        let date_formatter = DateFormatter()
        date_formatter.dateFormat = "dd.MM.yyyy"
        let today_date = date_formatter.string(from: today)
        for goal in decoded.goals
        {
            let daily = Date(timeIntervalSince1970: Double(goal.daily))
            let daily_date = date_formatter.string(from: daily)
            
            if (today_date == daily_date)
            {
                self.goals.append(goal)
            }
        }
    }
    
    func fetch_goals() -> Array<Goal>
    {
        do {
            try sync_loader()
        } catch {
            print("Error info: \(error)")
        }
        return self.goals
    }
}

class GoalsViewModel : ObservableObject
{
    @Published var goals: Array<Goal> = []
    
    func initialise_goals()
    {
        let fetcher = GoalsFetcher()
        self.goals = fetcher.fetch_goals()
    }
}

struct ContentView: View {
    @StateObject private var goals_view_model = GoalsViewModel()
    
    var body: some View {
        VStack {
            Text("Todoist2")
            ForEach(goals_view_model.goals.indices, id: \.self) { index in
                Text(goals_view_model.goals[index].name).padding(.bottom, 10) // Adjust spacing between buttons
            }
        }
        .onAppear {
            goals_view_model.initialise_goals()
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}
