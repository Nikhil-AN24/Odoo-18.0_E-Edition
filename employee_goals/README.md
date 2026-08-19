# Employee Goals & Performance Management System

A comprehensive Odoo 18 module for managing employee goals, milestones, daily tracking, and performance ratings.

## Features

### 1. Goal Management
- **Multiple Goal Types**: Training, Performance, Custom, and Organizational goals
- **Pre-configured Training Programs**: WE, I&W, WTL, TSA, TTM, EPP, EPL, MLDP, Coaching & Mentoring
- **Goal Workflow**: Draft → Submit → Approve → In Progress → Completed
- **Self-Service Goal Creation**: Employees can create their own goals
- **Progress Tracking**: Automatic and manual progress calculation
- **Priority Management**: Set goal priorities (Low, Normal, High, Critical)

### 2. Milestone Management
- **Break Down Goals**: Convert goals into trackable milestones
- **Dependencies**: Set milestone dependencies
- **Approval Workflow**: Optional manager approval for milestone completion
- **Target Dates**: Track milestone deadlines and completion dates
- **Progress Monitoring**: Individual milestone progress tracking

### 3. Daily Tracking
- **Daily Updates**: Employees update progress daily
- **Status Indicators**: On Track, At Risk, Blocked, Completed
- **Time Tracking**: Track hours spent on goals
- **Challenges Logging**: Record blockers and support needed
- **Manager Feedback**: Managers can provide feedback on daily updates
- **Notifications**: Automatic notifications for blocked or at-risk goals

### 4. Performance Rating System
- **Rating Scales**: O (Outstanding), A+, A, B+, B, C, D
- **Periodic Evaluation**: Track ratings by period (annual, quarterly, monthly)
- **Goal Integration**: Automatic calculation of goal completion rates
- **Training Tracking**: Monitor training program completion
- **Multi-level Review**: Employee → Manager → HR approval workflow
- **Recommendations**: Promotion and salary increment recommendations
- **Comparison**: Year-over-year rating comparison

### 5. Employee Integration
- **Goal Statistics**: View goal counts and completion rates on employee form
- **Performance History**: Access performance ratings from employee record
- **LMI Status**: Track Leadership Management Index status
- **Employment Status**: A, B, C, D classification support

### 6. Security & Access Control
- **Three User Levels**:
  - **User**: Can view and manage own goals only
  - **Team Manager**: Can manage team goals and provide approvals
  - **Manager**: Full access to all goals and configurations
- **Record Rules**: Data segregation based on employee hierarchy
- **Field-level Security**: Restricted access to sensitive information

### 7. Reporting & Analytics
- **Goal Reports**: Comprehensive PDF reports for goals
- **Performance Reports**: Detailed performance rating reports
- **Dashboard Views**: Kanban, Calendar, Pivot, and Graph views
- **Analytics**: Goal completion trends, department performance, etc.

## Installation

1. Copy the `employee_goals` folder to your Odoo addons directory
2. Update Apps List in Odoo
3. Search for "Employee Goals & Performance Management"
4. Click Install

## Configuration

### 1. Assign User Rights
Go to Settings → Users & Companies → Users and assign:
- **User: Own Goals Only** - For regular employees
- **Manager: Team Goals** - For team managers
- **Manager: All Goals** - For HR/Admin

### 2. Goal Types
Pre-configured goal types are automatically loaded. You can add more at:
Goals & Performance → Configuration → Goal Types

### 3. Rating Scales
Pre-configured rating scales (O, A+, A, B+, B, C, D) are loaded automatically.

## Usage

### For Employees
1. Navigate to Goals & Performance → My Goals → My Goals
2. Click Create to add a new goal
3. Submit for manager approval
4. Add milestones and track daily progress
5. View performance ratings under My Performance Ratings

### For Managers
1. Review and approve team goals under Team Goals
2. Provide feedback on daily updates
3. Create performance ratings for team members
4. Monitor team progress and completion rates

### For HR/Admin
1. Access all goals across organization
2. Configure goal types and settings
3. Final approval of performance ratings
4. Generate reports and analytics

## Technical Details

- **Odoo Version**: 18.0
- **Dependencies**: base, hr, mail
- **License**: LGPL-3
- **Models**: 8 main models
- **Views**: 40+ XML views
- **Reports**: 2 PDF reports

## Support

For issues or questions, please contact your system administrator.

## Credits

**Author**: Your Company
**Version**: 18.0.1.0.0
**License**: LGPL-3
