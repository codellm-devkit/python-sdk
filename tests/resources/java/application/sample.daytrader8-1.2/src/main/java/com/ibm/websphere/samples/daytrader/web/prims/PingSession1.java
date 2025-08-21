/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;
import java.io.PrintWriter;

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingHTTPSession1 - SessionID tests fundamental HTTP session functionality by
 * creating a unique session ID for each individual user. The ID is stored in
 * the users session and is accessed and displayed on each user request.
 *
 */
@WebServlet(name = "PingSession1", urlPatterns = { "/servlet/PingSession1" })
public class PingSession1 extends HttpServlet {
    private static final long serialVersionUID = -3703858656588519807L;
    private static int count;
    // For each new session created, add a session ID of the form "sessionID:" +
    // count
    private static String initTime;
    private static int hitCount;

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest request, HttpServletResponse response) throws ServletException, IOException {
        HttpSession session = null;
        try {
            try {
                // get the users session, if the user does not have a session
                // create one.
                session = request.getSession(true);
            } catch (Exception e) {
                Log.error(e, "PingSession1.doGet(...): error getting session");
                // rethrow the exception for handling in one place.
                throw e;
            }

            // Get the session data value
            Integer ival = (Integer) session.getAttribute("sessiontest.counter");
            // if their is not a counter create one.
            if (ival == null) {
                ival = new Integer(count++);
                session.setAttribute("sessiontest.counter", ival);
            }
            String SessionID = "SessionID:" + ival.toString();

            // Output the page
            response.setContentType("text/html");
            response.setHeader("SessionKeyTest-SessionID", SessionID);

            PrintWriter out = response.getWriter();
            out.println("<html><head><title>HTTP Session Key Test</title></head><body><HR><BR><FONT size=\"+2\" color=\"#000066\">HTTP Session Test 1: Session Key<BR></FONT><FONT size=\"+1\" color=\"#000066\">Init time: "
                    + initTime + "</FONT><BR><BR>");
            hitCount++;
            out.println("<B>Hit Count: " + hitCount + "<BR>Your HTTP Session key is " + SessionID + "</B></body></html>");
        } catch (Exception e) {
            // log the excecption
            Log.error(e, "PingSession1.doGet(..l.): error.");
            // set the server responce to 500 and forward to the web app defined
            // error page
            response.sendError(500, "PingSession1.doGet(...): error. " + e.toString());
        }
    }

    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/

    @Override
    public String getServletInfo() {
        return "HTTP Session Key: Tests management of a read only unique id";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        count = 0;
        hitCount = 0;
        initTime = new java.util.Date().toString();

    }
}