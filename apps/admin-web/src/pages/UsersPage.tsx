/**
 * Users Page
 * User management and search
 */

export default function UsersPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">User Management</h1>
          <p className="mt-1 text-sm text-gray-500">
            Search and manage user accounts
          </p>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow">
        <div className="p-6 border-b border-gray-200">
          <div className="flex items-center space-x-4">
            <input
              type="search"
              placeholder="Search by email or user ID..."
              className="flex-1 px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700">
              Search
            </button>
          </div>
        </div>

        <div className="p-6">
          <div className="text-center text-gray-500 py-12">
            <p>Search for users to view details and perform actions</p>
            <p className="text-sm mt-2">Coming soon: User list, freeze/unfreeze, KYC status, etc.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
