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
package com.ibm.websphere.samples.daytrader.web.jsf;

import java.io.IOException;

import javax.servlet.Filter;
import javax.servlet.FilterChain;
import javax.servlet.FilterConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;
//import javax.servlet.annotation.WebFilter;
import javax.servlet.annotation.WebFilter;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

@WebFilter(filterName = "JSFLoginFilter", urlPatterns = "*.faces")
public class JSFLoginFilter implements Filter {

    public JSFLoginFilter() {
        super();
    }

    /**
     * @see Filter#init(FilterConfig)
     */
    private FilterConfig filterConfig = null;

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {
        this.filterConfig = filterConfig;
    }

    /**
     * @see Filter#doFilter(ServletRequest, ServletResponse, FilterChain)
     */
    @Override
    public void doFilter(ServletRequest req, ServletResponse resp, FilterChain chain) throws IOException, ServletException {
        if (filterConfig == null) {
            return;
        }

        HttpServletRequest request = (HttpServletRequest) req;
        HttpServletResponse response = (HttpServletResponse) resp;

        HttpSession session = request.getSession();
        String userID = (String) session.getAttribute("uidBean");

        // If user has not logged in and is trying access account information,
        // redirect to login page.
        if (userID == null) {
            String url = request.getServletPath();

            if (url.contains("home") || url.contains("account") || url.contains("portfolio") || url.contains("quote") || url.contains("order")
                    || url.contains("marketSummary")) {
                System.out.println("JSF service error: User Not Logged in");
                response.sendRedirect("welcome.faces");
                return;
            }
        }

        chain.doFilter(req, resp/* wrapper */);
    }

    /**
     * @see Filter#destroy()
     */
    @Override
    public void destroy() {
        this.filterConfig = null;
    }

}
